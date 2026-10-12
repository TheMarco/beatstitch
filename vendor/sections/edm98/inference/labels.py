LABEL_TO_ID = {
    "intro": 0,
    "outro": 5,
    "silence": 6,
    "breakdown": 36,
    "buildup": 69,
    "drop": 70,
}

ID_TO_LABEL = {value: key for key, value in LABEL_TO_ID.items()}

DATASET_LABEL_TO_DATASET_ID = {
    "EDMFormer": 9,
}

DATASET_ID_ALLOWED_LABEL_IDS = {
    9: [0, 5, 6, 36, 69, 70],
}


def build_label_mask(num_classes: int = 128, dataset_label: str = "EDMFormer"):
    import numpy as np

    dataset_id = DATASET_LABEL_TO_DATASET_ID[dataset_label]
    allowed_ids = DATASET_ID_ALLOWED_LABEL_IDS[dataset_id]
    mask = np.ones(num_classes, dtype=bool)
    mask[allowed_ids] = False
    return mask
