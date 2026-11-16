"""Built-in icon marks (lit doorway concepts P1-P8) as standalone SVG, for tools/make_icons.py --concept.

    svg("P3", "gold")  -> 64x64 SVG string on the graphite tile
"""

ACCENTS = {  # accent, highlight, shadow
    "gold": ("#d6aa4f", "#f6dc9a", "#7a5418"),
    "violet": ("#a98cfa", "#ddd2ff", "#4a2fa0"),
    "ember": ("#ec5f57", "#ffb4a6", "#84201b"),
    "teal": ("#3fc2b1", "#b4f4ea", "#0f5e54"),
}


def arch(x0, x1, ys, yb):
    r = (x1 - x0) / 2
    return "M%g %g V%g A%g %g 0 0 1 %g %g V%g Z" % (x0, yb, ys, r, r, x1, ys, yb)


def scale_arch(s):
    f = lambda x: round(32 + (x - 32) * s, 2)
    g = lambda y: round(40 + (y - 40) * s, 2)
    return arch(f(12), f(52), g(34), g(56))


def _mix(a, b, t):
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))


def rpoly(pts, r):
    """Polygon with rounded corners (radius r), as an SVG path."""
    import math
    n, d = len(pts), []
    for i in range(n):
        p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
        def toward(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            L = math.hypot(dx, dy)
            k = min(r, L / 2) / L
            return (a[0] + dx * k, a[1] + dy * k)
        a, b = toward(p1, p0), toward(p1, p2)
        d.append(("M" if i == 0 else "L") + "%.2f %.2f Q%.2f %.2f %.2f %.2f" % (a[0], a[1], p1[0], p1[1], b[0], b[1]))
    return " ".join(d) + " Z"


def _mark(cid, AC, HI, LO):
    if cid == "P1":
        return (f'''<linearGradient id="s" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#7a7a82"/><stop offset="1" stop-color="#3c3c42"/></linearGradient>
                <radialGradient id="l" cx=".5" cy="1" r="1"><stop offset="0" stop-color="{HI}"/><stop offset=".5" stop-color="{AC}"/><stop offset="1" stop-color="{LO}"/></radialGradient>
                <linearGradient id="f" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{AC}" stop-opacity=".5"/><stop offset="1" stop-color="{AC}" stop-opacity="0"/></linearGradient>''',
                f'''<path d="M21 50 L43 50 L60 64 L4 64 Z" fill="url(#f)"/>
                <path d="{arch(21, 43, 30, 50)}" fill="{AC}" opacity=".7" filter="url(#blur)"/>
                <path d="{arch(13, 51, 29, 50)}" fill="url(#s)"/>
                <path d="M13.6 29 A18.4 18.4 0 0 1 50.4 29" fill="none" stroke="#ffffff" stroke-opacity=".18" stroke-width="1"/>
                <path d="{arch(21, 43, 30, 50)}" fill="url(#l)"/>
                <path d="M6 50 H58" stroke="#46464c" stroke-width="1.2"/>''')
    if cid == "P2":
        return (f'''<radialGradient id="l" cx=".5" cy=".75" r=".8"><stop offset="0" stop-color="{HI}"/><stop offset="1" stop-color="{AC}"/></radialGradient>''',
                f'''<path d="M12 56 L25.2 47.3 M52 56 L38.8 47.3" stroke="#dcdce0" stroke-opacity=".22" stroke-width="1"/>
                <path d="{scale_arch(.34)}" fill="{AC}" filter="url(#blur)" opacity=".9"/>
                <path d="{scale_arch(1)}" fill="none" stroke="#dcdce0" stroke-opacity=".9" stroke-width="3.4" stroke-linejoin="round"/>
                <path d="{scale_arch(.72)}" fill="none" stroke="#dcdce0" stroke-opacity=".5" stroke-width="2.6" stroke-linejoin="round"/>
                <path d="{scale_arch(.5)}" fill="none" stroke="{AC}" stroke-width="2.2" stroke-linejoin="round"/>
                <path d="{scale_arch(.34)}" fill="url(#l)"/>''')
    if cid == "P3":
        return (f'''<linearGradient id="p" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f1f1f4"/><stop offset="1" stop-color="#9d9da5"/></linearGradient>
                <radialGradient id="l" cx=".5" cy="1" r="1.1"><stop offset="0" stop-color="{HI}"/><stop offset=".55" stop-color="{AC}"/><stop offset="1" stop-color="{LO}"/></radialGradient>''',
                f'''<path d="M18 15 A4 4 0 0 1 22 11 H36 A15 15 0 0 1 36 41 H28 V49 A4 4 0 0 1 24 53 H22 A4 4 0 0 1 18 49 Z" fill="url(#p)"/>
                <path d="{arch(28, 42, 25, 35)}" fill="{AC}" filter="url(#blur)" opacity=".6"/>
                <path d="{arch(28, 42, 25, 35)}" fill="url(#l)"/>''')
    if cid == "P4":
        return (f'''<radialGradient id="e" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{HI}" stop-opacity=".95"/><stop offset=".55" stop-color="{AC}" stop-opacity=".55"/><stop offset="1" stop-color="{LO}" stop-opacity=".1"/></radialGradient>
                <linearGradient id="r" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{HI}"/><stop offset=".5" stop-color="{AC}"/><stop offset="1" stop-color="{LO}"/></linearGradient>''',
                f'''<g transform="rotate(-16 32 33)">
                <ellipse cx="32" cy="33" rx="17" ry="23" fill="{AC}" opacity=".35" filter="url(#blur)"/>
                <ellipse cx="32" cy="33" rx="15" ry="21" fill="url(#e)"/>
                <ellipse cx="32" cy="33" rx="17" ry="23" fill="none" stroke="url(#r)" stroke-width="5"/>
                <path d="M19 22 A17 23 0 0 1 30 10.2" fill="none" stroke="#ffffff" stroke-opacity=".55" stroke-width="1.6" stroke-linecap="round"/></g>''')
    if cid == "P5":
        return (f'''<linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{AC}"/><stop offset=".7" stop-color="{HI}"/><stop offset="1" stop-color="{HI}"/></linearGradient>
                <linearGradient id="m" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{AC}" stop-opacity=".45"/><stop offset="1" stop-color="{AC}" stop-opacity="0"/></linearGradient>
                <radialGradient id="v" cx=".5" cy=".62" r=".55"><stop offset="0" stop-color="{AC}" stop-opacity=".22"/><stop offset="1" stop-color="{AC}" stop-opacity="0"/></radialGradient>''',
                f'''<rect x="0" y="0" width="64" height="64" fill="url(#v)"/>
                <path d="{arch(26, 38, 22, 46)}" fill="{AC}" filter="url(#blur)"/>
                <path d="{arch(26, 38, 22, 46)}" fill="url(#g)"/>
                <path d="M26 46 V62 H38 V46 Z" fill="url(#m)"/>
                <path d="M8 46 H56" stroke="#ffffff" stroke-opacity=".12" stroke-width="1"/>''')
    if cid == "P6":
        door = '<path d="M.28 1 V.55 A.22 .22 0 0 1 .72 .55 V1 Z" fill="%s"%s/>'
        return (f'''<linearGradient id="t2" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#6a6a71"/><stop offset="1" stop-color="#4a4a50"/></linearGradient>
                <linearGradient id="l2" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#3b3b41"/><stop offset="1" stop-color="#2a2a2f"/></linearGradient>
                <linearGradient id="r2" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#46464c"/><stop offset="1" stop-color="#36363b"/></linearGradient>
                <linearGradient id="d" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{AC}"/><stop offset="1" stop-color="{HI}"/></linearGradient>''',
                f'''<path d="M32 8 L53 20 L32 32 L11 20 Z" fill="url(#t2)"/>
                <path d="M11 20 L32 32 L32 56 L11 44 Z" fill="url(#l2)"/>
                <path d="M32 32 L53 20 L53 44 L32 56 Z" fill="url(#r2)"/>
                <path d="M32 8 L53 20 M32 32 L11 20 M32 32 L53 20" stroke="#ffffff" stroke-opacity=".12" stroke-width=".8"/>
                <g transform="matrix(21 12 0 24 11 20)">{door % (AC, ' filter="url(#blur)" opacity=".6"')}</g>
                <g transform="matrix(21 12 0 24 11 20)">{door % ("url(#d)", "")}</g>
                <path d="M15 58 L30 66 L22 66 Z" fill="{AC}" opacity=".18"/>''')
    if cid == "P7":
        return (f'''<radialGradient id="b" cx=".36" cy=".3" r=".75"><stop offset="0" stop-color="#e2e2e7"/><stop offset=".45" stop-color="#7c7c84"/><stop offset="1" stop-color="#26262a"/></radialGradient>
                <linearGradient id="r" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{LO}"/><stop offset=".5" stop-color="{HI}"/><stop offset="1" stop-color="{AC}"/></linearGradient>''',
                f'''<g transform="rotate(-18 32 34)"><path d="M9 34 A23 7.5 0 0 1 55 34" fill="none" stroke="{LO}" stroke-width="3.4" stroke-linecap="round"/></g>
                <circle cx="32" cy="33" r="13.5" fill="url(#b)"/>
                <g transform="rotate(-18 32 34)">
                <path d="M9 34 A23 7.5 0 0 0 55 34" fill="none" stroke="{AC}" stroke-width="7" stroke-linecap="round" opacity=".45" filter="url(#blur)"/>
                <path d="M9 34 A23 7.5 0 0 0 55 34" fill="none" stroke="url(#r)" stroke-width="3.6" stroke-linecap="round"/></g>''')
    if cid == "P8":
        return (f'''<clipPath id="w"><path d="{arch(16, 48, 30, 54)}"/></clipPath>
                <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#202024"/><stop offset=".75" stop-color="{LO}"/><stop offset="1" stop-color="{AC}"/></linearGradient>
                <radialGradient id="sun" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="#ffffff"/><stop offset=".35" stop-color="{HI}"/><stop offset="1" stop-color="{AC}"/></radialGradient>''',
                f'''<g clip-path="url(#w)">
                <rect x="14" y="12" width="36" height="34" fill="url(#sky)"/>
                <circle cx="32" cy="46" r="13" fill="{AC}" opacity=".6" filter="url(#blur)"/>
                <circle cx="32" cy="46" r="8" fill="url(#sun)"/>
                <rect x="14" y="46" width="36" height="10" fill="#151517"/>
                <path d="M14 46 H50" stroke="{HI}" stroke-width="1.2"/></g>
                <path d="{arch(16, 48, 30, 54)}" fill="none" stroke="#dcdce0" stroke-width="3" stroke-linejoin="round"/>''')
    raise KeyError("Unknown concept %s (P1-P8)" % cid)


def svg(concept, accent="gold"):
    """64x64 SVG on the graphite glass tile."""
    AC, HI, LO = ACCENTS[accent] if accent in ACCENTS else (accent, accent, accent)
    defs, body = _mark(concept.upper(), AC, HI, LO)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="64" height="64">
  <defs>
    <linearGradient id="t" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f2f33"/><stop offset="1" stop-color="#1b1b1e"/></linearGradient>
    <clipPath id="c"><rect x="1" y="1" width="62" height="62" rx="14"/></clipPath>
    <linearGradient id="rim" x1="0" y1="0" x2=".6" y2="1"><stop offset="0" stop-color="#ffffff" stop-opacity=".28"/><stop offset=".45" stop-color="#ffffff" stop-opacity=".07"/><stop offset="1" stop-color="#ffffff" stop-opacity=".14"/></linearGradient>
    <filter id="blur" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="3.2"/></filter>
    {defs}
  </defs>
  <rect x="1" y="1" width="62" height="62" rx="14" fill="url(#t)"/>
  <g clip-path="url(#c)">{body}</g>
  <rect x="1.5" y="1.5" width="61" height="61" rx="13.5" fill="none" stroke="url(#rim)"/>
</svg>'''
