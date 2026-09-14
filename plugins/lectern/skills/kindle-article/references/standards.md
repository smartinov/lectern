# Kindle and EPUB standards

Use these current primary sources when maintaining this skill:

- [Kindle Publishing Guidelines](https://kdp.amazon.com/en_US/help/topic/GU72M65VRFPH43L6)
- [Kindle navigation guidelines](https://kdp.amazon.com/en_US/help/topic/GY3AD8C6C6GAG42N)
- [Kindle cover image guidelines](https://kdp.amazon.com/en_US/help/topic/G6GTK3T3NUHKLEFX)
- [Kindle reflowable text guidelines](https://kdp.amazon.com/en_US/help/topic/GH4DRT75GWWAGBTU)
- [Kindle reflowable image guidelines](https://kdp.amazon.com/en_US/help/topic/G75V4YX5X8GRGXWV)
- [Kindle reflowable table guidelines](https://kdp.amazon.com/en_US/help/topic/GZ8BAXASXKB5JVML)
- [Kindle QA standards](https://kdp.amazon.com/en_US/help/topic/GGRXLC5USU4H67YM)
- [Send to Kindle supported formats](https://www.amazon.co.uk/gp/help/customer/display.html?nodeId=G5WYD9SAF7PGXRNA)
- [EPUB 3.3](https://www.w3.org/TR/epub-33/)
- [EPUB Accessibility Techniques 1.1](https://www.w3.org/TR/epub-a11y-tech-11/)
- [EPUBCheck](https://github.com/w3c/epubcheck)

## Encoded requirements

- EPUB 3.3 requires exactly one navigation document. The default reading order
  comes from the spine, so the navigation document can remain outside it while
  still powering a reading system's logical contents menu.
- Kindle retains NCX support and prefers EPUB3 navigation when both exist.
- Amazon warns that adding an HTML cover page alongside the cover-image
  metadata can duplicate the cover. Keep only one cover-image manifest item.
- Use a 1600×2560 RGB JPEG under 5 MB. Cover text must match package metadata.
- Reflowable body text must not force the reader's font family, size, line
  height, alignment, foreground color, or background color. Headings and
  special blocks may use relative styling.
- Content documents and package metadata identify their language. Images have
  contextual alternative text; decorative images use an explicit empty alt.
- Amazon recommends fewer than 300 HTML files and requires every individual
  HTML file to be smaller than 30 MB.
- EPUBCheck establishes EPUB conformance. Resolve every error and warning
  before delivery.

The configured 14 MiB mail limit is deliberately stricter than Amazon's other
Send to Kindle channels. It prevents accidental Mail Drop and is the binding
limit for this workflow.
