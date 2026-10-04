# Bundled launcher resources

The raster images and fonts in this directory were extracted from the local
`ComfyUI-aki-v3/.launcher/StableDiffusionWebUILauncher.Resources.dll` using
.NET's managed resource reader.  The DLL itself is not imported or loaded at
runtime; only these files are used by Aura-Rift.

`hanabi.jpg` is the default launch banner and `icon_minimi.png` is the
launcher avatar.  The `head_images/` variants and `about_bg*.jpg` files are
available to pages that want to rotate banners.  `cascadiamono.ttf` and
`segmdl2.ttf` are optional application fonts; the UI falls back to system
fonts when Qt cannot register them.

See `aura_rift.resources` for lookup helpers that work from a source checkout,
a wheel, or a zip importer.
