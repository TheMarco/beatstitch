import numpy as np
import torch
import torch.nn.functional as F
from numpy.lib.stride_tricks import sliding_window_view


def local_maxima(tensor, filter_size=41):
    assert len(tensor.shape) in (1, 2), "Input tensor should have 1 or 2 dimensions"
    assert filter_size % 2 == 1, "Filter size should be an odd number"

    original_shape = tensor.shape
    if len(original_shape) == 1:
        tensor = tensor.unsqueeze(0)

    padding = filter_size // 2
    padded_arr = F.pad(tensor, (padding, padding), mode="constant", value=-torch.inf)
    rolling_view = padded_arr.unfold(1, filter_size, 1)

    center = filter_size // 2
    local_maxima_mask = torch.eq(
        rolling_view[:, :, center], torch.max(rolling_view, dim=-1).values
    )
    local_maxima_indices = local_maxima_mask.nonzero()

    output_arr = torch.zeros_like(tensor)
    output_arr[local_maxima_mask] = tensor[local_maxima_mask]
    output_arr = output_arr.reshape(original_shape)

    return output_arr, local_maxima_indices


def peak_picking(boundary_activation, window_past=12, window_future=6):
    window_size = window_past + window_future
    assert window_size % 2 == 0, "window_past + window_future must be even"
    window_size += 1

    boundary_activation_padded = np.pad(
        boundary_activation, (window_past, window_future), mode="constant"
    )
    max_filter = sliding_window_view(boundary_activation_padded, window_size)
    local_maxima_mask = (boundary_activation == np.max(max_filter, axis=-1)) & (
        boundary_activation > 0
    )

    past_window_filter = sliding_window_view(
        boundary_activation_padded[: -(window_future + 1)], window_past
    )
    future_window_filter = sliding_window_view(
        boundary_activation_padded[window_past + 1 :], window_future
    )
    past_mean = np.mean(past_window_filter, axis=-1)
    future_mean = np.mean(future_window_filter, axis=-1)
    strength_values = boundary_activation - ((past_mean + future_mean) / 2)

    boundary_candidates = np.flatnonzero(local_maxima_mask)
    strength_values = strength_values[boundary_candidates]

    strength_activations = np.zeros_like(boundary_activation)
    strength_activations[boundary_candidates] = strength_values
    return strength_activations
