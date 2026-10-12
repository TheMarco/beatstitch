"""Memory-efficient inference for the EDMFormer prototype's two encoders.

Preserves the rotary/projection order of Transformers 4.57's
Wav2Vec2ConformerSelfAttention. PyTorch SDPA avoids materializing the full
time-by-time attention matrix. Training and requested attention weights keep
the upstream implementation; unsupported positional encodings stay untouched.
"""
from types import MethodType


def query_chunk_attention(query, key, value, mask=None, chunk=256):
    """Bound attention scratch space while every query still sees every key."""
    import torch
    import torch.nn.functional as F
    outputs = []
    for first in range(0, query.shape[-2], chunk):
        last = min(query.shape[-2], first + chunk)
        part_mask = mask if mask is None or mask.ndim < 2 or mask.shape[-2] == 1 else mask[..., first:last, :]
        outputs.append(F.scaled_dot_product_attention(query[..., first:last, :], key, value,
                                                     attn_mask=part_mask, dropout_p=0.0))
    return torch.cat(outputs, dim=-2)


def enable_efficient_attention(model):
    import torch.nn.functional as F
    from transformers.models.wav2vec2_conformer.modeling_wav2vec2_conformer import Wav2Vec2ConformerSelfAttention

    def forward(self, hidden_states, attention_mask=None, relative_position_embeddings=None,
                output_attentions=False):
        if self.training or output_attentions:
            return self._dj_original_attention(hidden_states, attention_mask, relative_position_embeddings, output_attentions)
        if relative_position_embeddings is None:
            raise ValueError('Rotary position embeddings are required.')
        batch, length, _ = hidden_states.shape
        rotated = self._apply_rotary_embedding(hidden_states, relative_position_embeddings)

        def project(layer, states):
            return layer(states).reshape(batch, length, self.num_heads, self.head_size).transpose(1, 2)

        query, key = project(self.linear_q, rotated), project(self.linear_k, rotated)
        value = project(self.linear_v, hidden_states)
        if query.device.type == 'mps' and length > 256:
            # MPS can use the SDPA math backend, which still allocates a full
            # attention matrix. Query slicing bounds it without truncating the
            # keys/values or changing a query's full-song musical context.
            result = query_chunk_attention(query, key, value, attention_mask)
        else:
            result = F.scaled_dot_product_attention(query, key, value, attn_mask=attention_mask, dropout_p=0.0)
        result = result.transpose(1, 2).reshape(batch, length, self.num_heads * self.head_size)
        return self.linear_out(result), None

    count = 0
    for layer in model.modules():
        if not isinstance(layer, Wav2Vec2ConformerSelfAttention) or layer.position_embeddings_type != 'rotary':
            continue
        if not hasattr(layer, '_dj_original_attention'):
            layer._dj_original_attention = layer.forward
            layer.forward = MethodType(forward, layer)
        count += 1
    return count


def enable_bounded_frontend(block, chunk_frames=1024):
    """Chunk the fixed two-stage convolution, preserving its 19-frame field.

    Each result frame is centered on input frame 4*i and depends on nine input
    frames on either side. A 12-frame halo preserves that context and the
    stride phase. Batch normalization uses stored statistics during inference.
    The later transformer still sees every frame of the original full window.
    """
    import torch
    if not isinstance(chunk_frames, int) or chunk_frames < 4 or chunk_frames % 4:
        raise ValueError('Chunk size must be a positive multiple of four frames.')
    if len(block.conv) != 2:
        raise ValueError('Expected the two-stage MusicFM/MuQ convolution.')
    for stage in block.conv:
        for name, stride in (('conv1', (2, 2)), ('conv2', (1, 1)), ('conv3', (2, 2))):
            conv = getattr(stage, name)
            if (conv.kernel_size != (3, 3) or conv.stride != stride or conv.padding != (1, 1)
                    or conv.dilation != (1, 1)):
                raise ValueError('Unexpected frontend receptive field.')
    if hasattr(block, '_dj_original_subsampling'):
        return
    block._dj_original_subsampling = block.forward

    def forward(self, states):
        length = states.shape[-1]
        if self.training or length <= chunk_frames:
            return self._dj_original_subsampling(states)
        outputs = []
        total, count = (length + 3) // 4, chunk_frames // 4
        for first in range(0, total, count):
            last = min(total, first + count)
            start, end = max(0, first * 4 - 12), min(length, last * 4 + 12)
            result = self._dj_original_subsampling(states[..., start:end])
            offset = (first * 4 - start) // 4
            # Copy the small final features so the slice cannot retain other
            # chunk outputs on the allocator while the next chunk runs.
            outputs.append(result[:, offset:offset + last - first].clone())
        return torch.cat(outputs, dim=1)

    block.forward = MethodType(forward, block)
