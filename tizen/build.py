from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


def build(output: Path | None = None) -> Path:
    source = Path(__file__).resolve().parent
    destination = output or source / 'dist' / 'cinemateca.wgt'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as widget:
        for name in ('config.xml', 'index.html', 'style.css', 'subtitles.js', 'app.js'):
            widget.write(source / name, name)
    return destination


if __name__ == '__main__':
    print(build())
