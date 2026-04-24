# Icons

## Files

| File | Use | Format requirement |
|---|---|---|
| `harness.svg` | Activity-bar container + chat participant | Single-color SVG; must reference `currentColor` so VS Code theming works. Monochrome. |
| `harness-hero.svg` | Source for the marketplace PNG | 256×256 SVG, full colour, rounded dark tile + amber output branch. Not shipped directly — rasterise to `harness-hero.png` for the `.vsix`. |
| `harness-hero.png` | Marketplace listing / extension catalog | 128×128 minimum, 256×256 recommended, PNG. Generated from `harness-hero.svg`. Referenced by `package.json` top-level `icon`. |

## Rationale

Pure abstraction — no letters. Three inputs (planner, designer, coder)
flow into a single anchor node (the harness); one amber output
(reviewer / shipped change) leaves from the bottom. The mark is literally
the pipeline, which reads well at 16px (activity bar) and 256px
(marketplace tile).

## Generating the hero PNG

The Marketplace icon (top-level `package.json` `icon` field) must be a
PNG — VS Code rejects SVG there. To add the hero icon to the
marketplace listing:

```bash
# librsvg — cleanest rasterisation of gradients + stroke caps
rsvg-convert -w 256 -h 256 media/icons/harness-hero.svg \
    -o media/icons/harness-hero.png

# ImageMagick fallback
convert -background none -density 384 media/icons/harness-hero.svg \
    -resize 256x256 media/icons/harness-hero.png
```

Then add this line to `package.json` (just below `"categories"`):

```json
  "icon": "media/icons/harness-hero.png",
```

Commit both the PNG and the package.json change together. The `icon`
field is intentionally absent from the current package.json because
PNG generation needs a machine with librsvg or ImageMagick installed —
shipping a missing-file reference would break `vsce package`.

The activity-bar icon (`harness.svg`) is a single-color SVG — it ships
as-is and doesn't need any rasterisation.

## Updating the mark

`harness.svg` is the source of truth for the activity-bar glyph.
`harness-hero.svg` is a separate file so we can add the background tile
and amber accent without breaking the single-color contract the
activity-bar icon has to honour.
