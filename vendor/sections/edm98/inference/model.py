import torch
import torch.nn as nn

from x_transformers import Encoder

from .postprocess import postprocess_functional_structure


class Head(nn.Module):
    def __init__(self, input_dim, output_dim, hidden_dims=None, activation="silu"):
        super().__init__()
        hidden_dims = hidden_dims or []
        act_layers = {"relu": nn.ReLU, "silu": nn.SiLU, "gelu": nn.GELU}
        act_layer = act_layers.get(activation.lower())
        if not act_layer:
            raise ValueError(f"Unsupported activation: {activation}")

        dims = [input_dim] + hidden_dims + [output_dim]
        layers = []
        for i in range(len(dims) - 1):
            layers.append(nn.Linear(dims[i], dims[i + 1]))
            if i < len(dims) - 2:
                layers.append(act_layer())
        self.net = nn.Sequential(*layers)

    def forward(self, x):
        batch, t_steps, channels = x.shape
        x = x.reshape(-1, channels)
        x = self.net(x)
        return x.reshape(batch, t_steps, -1)


class WrapedTransformerEncoder(nn.Module):
    def __init__(
        self, input_dim, transformer_input_dim, num_layers=1, nhead=8, dropout=0.1
    ):
        super().__init__()
        if input_dim != transformer_input_dim:
            self.input_proj = nn.Sequential(
                nn.Linear(input_dim, transformer_input_dim),
                nn.LayerNorm(transformer_input_dim),
                nn.GELU(),
                nn.Dropout(dropout * 0.5),
                nn.Linear(transformer_input_dim, transformer_input_dim),
            )
        else:
            self.input_proj = nn.Identity()

        self.transformer = Encoder(
            dim=transformer_input_dim,
            depth=num_layers,
            heads=nhead,
            layer_dropout=dropout,
            attn_dropout=dropout,
            ff_dropout=dropout,
            attn_flash=True,
            rotary_pos_emb=True,
        )

    def forward(self, x, src_key_padding_mask=None):
        x = self.input_proj(x)
        mask = (
            ~torch.tensor(src_key_padding_mask, dtype=torch.bool, device=x.device)
            if src_key_padding_mask is not None
            else None
        )
        return self.transformer(x, mask=mask)


class TimeDownsample(nn.Module):
    def __init__(
        self, dim_in, dim_out=None, kernel_size=5, stride=5, padding=0, dropout=0.1
    ):
        super().__init__()
        self.dim_out = dim_out or dim_in
        assert self.dim_out % 2 == 0

        self.depthwise_conv = nn.Conv1d(
            in_channels=dim_in,
            out_channels=dim_in,
            kernel_size=kernel_size,
            stride=stride,
            padding=padding,
            groups=dim_in,
            bias=False,
        )
        self.pointwise_conv = nn.Conv1d(
            in_channels=dim_in,
            out_channels=self.dim_out,
            kernel_size=1,
            bias=False,
        )
        self.pool = nn.AvgPool1d(kernel_size, stride, padding=padding)
        self.norm1 = nn.LayerNorm(self.dim_out)
        self.act1 = nn.GELU()
        self.dropout1 = nn.Dropout(dropout)

        if dim_in != self.dim_out:
            self.residual_conv = nn.Conv1d(dim_in, self.dim_out, kernel_size=1, bias=False)
        else:
            self.residual_conv = None

    def forward(self, x):
        residual = x
        x_c = x.transpose(1, 2)
        x_c = self.depthwise_conv(x_c)
        x_c = self.pointwise_conv(x_c)

        res = self.pool(residual.transpose(1, 2))
        if self.residual_conv:
            res = self.residual_conv(res)
        x_c = x_c + res
        x_c = x_c.transpose(1, 2)
        x_c = self.norm1(x_c)
        x_c = self.act1(x_c)
        x_c = self.dropout1(x_c)
        return x_c


class AddFuse(nn.Module):
    def forward(self, x, cond):
        return x + cond


class Model(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.config = config

        self.input_norm = nn.LayerNorm(config.input_dim)
        self.mixed_win_downsample = nn.Linear(config.input_dim_raw, config.input_dim)
        self.dataset_class_prefix = nn.Embedding(
            num_embeddings=config.num_dataset_classes,
            embedding_dim=config.transformer_encoder_input_dim,
        )
        self.down_sample_conv = TimeDownsample(
            dim_in=config.input_dim,
            dim_out=config.transformer_encoder_input_dim,
            kernel_size=config.down_sample_conv_kernel_size,
            stride=config.down_sample_conv_stride,
            dropout=config.down_sample_conv_dropout,
            padding=config.down_sample_conv_padding,
        )
        self.add_fuse = AddFuse()
        self.transformer = WrapedTransformerEncoder(
            input_dim=config.transformer_encoder_input_dim,
            transformer_input_dim=config.transformer_input_dim,
            num_layers=config.num_transformer_layers,
            nhead=config.transformer_nhead,
            dropout=config.transformer_dropout,
        )
        self.boundary_head = Head(config.transformer_input_dim, 1)
        self.function_head = Head(config.transformer_input_dim, config.num_classes)

    def infer(
        self,
        input_embeddings,
        dataset_ids,
        label_id_masks,
        with_logits=False,
    ):
        with torch.no_grad():
            input_embeddings = self.mixed_win_downsample(input_embeddings)
            input_embeddings = self.input_norm(input_embeddings)
            logits = self.down_sample_conv(input_embeddings)

            dataset_prefix = self.dataset_class_prefix(dataset_ids)
            dataset_prefix_expand = dataset_prefix.unsqueeze(1).expand(
                logits.size(0), 1, -1
            )
            logits = self.add_fuse(x=logits, cond=dataset_prefix_expand)
            logits = self.transformer(x=logits, src_key_padding_mask=None)

            function_logits = self.function_head(logits)
            boundary_logits = self.boundary_head(logits).squeeze(-1)

            logits = {
                "function_logits": function_logits,
                "boundary_logits": boundary_logits,
            }

            expanded_mask = label_id_masks.expand(
                -1, logits["function_logits"].size(1), -1
            )
            logits["function_logits"] = logits["function_logits"].masked_fill(
                expanded_mask, -float("inf")
            )

            msa_info = postprocess_functional_structure(logits=logits, config=self.config)

        return (msa_info, logits) if with_logits else msa_info
