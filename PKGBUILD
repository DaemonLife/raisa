# Maintainer: DaemonLife <https://github.com/DaemonLife>
# Arch Linux package built right from this folder: makepkg -si
pkgname=raisa
pkgver=1.0
pkgrel=1
pkgdesc="Terminal client for the SCP Wiki: articles, bookmarks, search, inline images"
arch=('any')
url="https://github.com/DaemonLife/raisa"
license=('GPL-3.0-or-later')
depends=('python' 'python-beautifulsoup4')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
checkdepends=('python-pytest')
optdepends=('python-pillow: inline images'
            'librsvg: inline SVG images'
            'xdg-utils: open links and images in the browser and image viewer')

build() {
  cd "$startdir"
  python -m build --wheel --no-isolation --outdir "$srcdir/dist"
}

check() {
  cd "$startdir"
  python -m pytest -q tests
}

package() {
  python -m installer --destdir="$pkgdir" "$srcdir"/dist/*.whl
  install -Dm644 "$startdir/raisa.1" "$pkgdir/usr/share/man/man1/raisa.1"
}
