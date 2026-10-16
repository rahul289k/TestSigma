# PR #6707: fix(common): keep HTML response preview readable in dark theme

**Low risk.** This change modifies how HTML responses are displayed in the preview pane to ensure text remains visible when using the dark theme.

The HTML response preview now forces a light background color scheme by default, preventing text from becoming invisible when the app is set to dark mode. This ensures that unstyled HTML content remains readable regardless of the user's theme settings. The update also includes internal improvements to how these previews are constructed.

## Retest these first


## Screens affected


## Product requirements


## Needs a human

- The HTML preview renderer is used within the REST request response tab, but we have not verified its behavior on screen. Could you confirm which specific screen displays this preview and if we should perform a manual check to ensure the text contrast is correct?

## Changes we could not connect to a screen

- Lenses › Renderers › HTMLLens Renderer: can appear on the REST page, but the crawl never showed any of its controls
