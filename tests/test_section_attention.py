"""Run in the isolated EDMFormer environment; compares actual upstream layers."""
import copy
import unittest

import torch
try:
    from transformers import Wav2Vec2ConformerConfig
    from transformers.models.wav2vec2_conformer.modeling_wav2vec2_conformer import (
        Wav2Vec2ConformerSelfAttention, Wav2Vec2ConformerRotaryPositionalEmbedding)
except ModuleNotFoundError:
    raise unittest.SkipTest('The optional EDMFormer prototype environment is required.')

from studio_section_attention import enable_efficient_attention, enable_bounded_frontend, query_chunk_attention


class EfficientAttentionTests(unittest.TestCase):
    def test_query_chunks_preserve_global_attention_and_per_query_masks(self):
        torch.manual_seed(81)
        query, key, value = [torch.randn(2, 4, 301, 8) for _ in range(3)]
        full_mask = torch.zeros(2, 1, 301, 301)
        full_mask[:, :, 100:, :50] = -10000
        for mask in (None, full_mask, full_mask[:, :, :1, :]):
            with self.subTest(mask=mask is not None), torch.inference_mode():
                scores = query @ key.transpose(-2, -1) / (8**.5)
                if mask is not None: scores += mask
                expected = scores.softmax(-1) @ value
                actual = query_chunk_attention(query, key, value, mask, chunk=64)
                torch.testing.assert_close(actual, expected, atol=4e-7, rtol=1e-5)

    def test_frontend_chunk_edges_and_partial_final_frames_match_whole_input(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path('outputs/edmformer-research').resolve()))
        from musicfm.modules.conv import Conv2dSubsampling as MusicFMConv
        from muq.muq.modules.conv import Conv2dSubsampling as MuQConv
        torch.manual_seed(21)
        for cls in (MusicFMConv, MuQConv):
            original = cls(1, 8, 16, n_bands=32).eval(); bounded = copy.deepcopy(original)
            enable_bounded_frontend(bounded, 64)
            for length in (1, 63, 64, 65, 127, 128, 129, 260, 261):
                with self.subTest(model=cls, length=length), torch.inference_mode():
                    values = torch.randn(2, 32, length)
                    # Sharp changes at chunk boundaries expose padding errors.
                    values[..., 63:66] += 3
                    expected, actual = original(values), bounded(values)
                    self.assertEqual(actual.shape, (2, (length + 3)//4, 16))
                    torch.testing.assert_close(actual, expected, atol=2e-7, rtol=1e-5)
            bad = cls(1, 8, 16, strides=[1, 2], n_bands=32)
            with self.assertRaises(ValueError): enable_bounded_frontend(bad)

    def test_upstream_equivalence_with_rotary_positions_and_padding(self):
        torch.manual_seed(572)
        config = Wav2Vec2ConformerConfig(hidden_size=32, num_attention_heads=4,
                                        attention_dropout=.1, position_embeddings_type='rotary')
        original = Wav2Vec2ConformerSelfAttention(config).eval()
        optimized = copy.deepcopy(original)
        self.assertEqual(enable_efficient_attention(optimized), 1)
        self.assertEqual(enable_efficient_attention(optimized), 1)
        for length in (1, 37, 750):
            with self.subTest(length=length), torch.inference_mode():
                states = torch.randn(2, length, 32)
                positions = Wav2Vec2ConformerRotaryPositionalEmbedding(config)(states)
                mask = torch.zeros(2, 1, 1, length)
                if length > 1: mask[0, :, :, -length//3:] = -10000
                for attention_mask in (None, mask):
                    expected = original(states, attention_mask, positions)[0]
                    actual, probabilities = optimized(states, attention_mask, positions)
                    torch.testing.assert_close(actual, expected, atol=3e-7, rtol=1e-5)
                    self.assertIsNone(probabilities)
        self.assertEqual(original.state_dict().keys(), optimized.state_dict().keys())

    def test_attention_outputs_and_training_keep_upstream_behavior(self):
        config = Wav2Vec2ConformerConfig(hidden_size=32, num_attention_heads=4,
                                        attention_dropout=.2, position_embeddings_type='rotary')
        original = Wav2Vec2ConformerSelfAttention(config).eval(); optimized = copy.deepcopy(original)
        enable_efficient_attention(optimized)
        states = torch.randn(1, 21, 32)
        positions = Wav2Vec2ConformerRotaryPositionalEmbedding(config)(states)
        for training, outputs in ((False, True), (True, False)):
            original.train(training); optimized.train(training)
            torch.manual_seed(48); expected = original(states, relative_position_embeddings=positions, output_attentions=outputs)
            torch.manual_seed(48); actual = optimized(states, relative_position_embeddings=positions, output_attentions=outputs)
            for a, b in zip(actual, expected): torch.testing.assert_close(a, b, atol=0, rtol=0)
        relative = Wav2Vec2ConformerSelfAttention(Wav2Vec2ConformerConfig(position_embeddings_type='relative'))
        self.assertEqual(enable_efficient_attention(relative), 0)


if __name__ == '__main__': unittest.main()
