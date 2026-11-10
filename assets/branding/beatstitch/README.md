# Beatstitch logo artwork

All SVGs use genuine vector paths and gradients. Lettering is outlined, so no
font installation is needed. There are no embedded images or external resources.

- `beatstitch-horizontal.svg` and `beatstitch-stacked.svg`: transparent artwork
  with ivory lettering, for dark backgrounds.
- `beatstitch-horizontal-light.svg` and `beatstitch-stacked-light.svg`:
  transparent artwork with dark lettering, for light backgrounds.
- `beatstitch-horizontal-dark.svg` and `beatstitch-stacked-dark.svg`: complete
  lockups including a charcoal background.
- `beatstitch-symbol.svg`: transparent standalone emblem.
- `beatstitch-wordmark.svg`: transparent standalone ivory wordmark.

The approved glossy artwork is reconstructed as editable vector shapes with
gradient shading. The raster enamel texture and glow are approximated rather
than embedded. The lettering is rebuilt as clean geometric curves in the same
bold extended style, retaining the reference's placement and proportions.
Change the `wordmark` group's fill to recolor the lettering. The emblem's named
groups and gradient stops can be edited independently.

`beatstitch-app-icon.png` preserves the originally selected icon.
`reference-horizontal.png` and `reference-stacked.png` preserve the generated
dark-background logo concepts. `imagegen-prompts.json` records their prompts.

The SVGs were parsed, checked for raster/font dependencies, rendered with
librsvg, and visually inspected. Transparent exports were checked for real alpha.
Rebuild helpers are in `tools/build_beatstitch_lockups.py` and
`tools/beatstitch_vector_symbol.py`; their image tracing needs Pillow and NumPy.
