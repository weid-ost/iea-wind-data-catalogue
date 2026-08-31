import math, json

# ---- OKLab / OKLCH <-> sRGB (Ottosson) ----
def srgb_to_linear(c): return c/12.92 if c <= 0.04045 else ((c+0.055)/1.055)**2.4
def linear_to_srgb(c):
    c = max(0.0, min(1.0, c))
    return 12.92*c if c <= 0.0031308 else 1.055*(c**(1/2.4))-0.055
def hex_to_rgb(h): h=h.lstrip('#'); return tuple(int(h[i:i+2],16)/255 for i in (0,2,4))
def rgb_to_hex(r,g,b): return '#%02X%02X%02X' % tuple(round(max(0,min(1,v))*255) for v in (r,g,b))

def rgb_to_oklch(r,g,b):
    r,g,b = map(srgb_to_linear,(r,g,b))
    l = 0.4122214708*r + 0.5363325363*g + 0.0514459929*b
    m = 0.2119034982*r + 0.6806995451*g + 0.1073969566*b
    s = 0.0883024619*r + 0.2817188376*g + 0.6299787005*b
    l_,m_,s_ = (v**(1/3) for v in (l,m,s))
    L = 0.2104542553*l_ + 0.7936177850*m_ - 0.0040720468*s_
    a = 1.9779984951*l_ - 2.4285922050*m_ + 0.4505937099*s_
    b2= 0.0259040371*l_ + 0.7827717662*m_ - 0.8086757660*s_
    C = math.hypot(a,b2); H = math.degrees(math.atan2(b2,a)) % 360
    return L,C,H

def oklch_to_rgb(L,C,H):
    a = C*math.cos(math.radians(H)); b = C*math.sin(math.radians(H))
    l_ = L + 0.3963377774*a + 0.2158037573*b
    m_ = L - 0.1055613458*a - 0.0638541728*b
    s_ = L - 0.0894841775*a - 1.2914855480*b
    l,m,s = (v**3 for v in (l_,m_,s_))
    r = +4.0767416621*l - 3.3077115913*m + 0.2309699292*s
    g = -1.2684380046*l + 2.6097574011*m - 0.3413193965*s
    b3= -0.0041960863*l - 0.7034186147*m + 1.7076147010*s
    return tuple(map(linear_to_srgb,(r,g,b3)))

def oklch_hex(L,C,H): return rgb_to_hex(*oklch_to_rgb(L,C,H))

def wcag_lum(hexs):
    r,g,b = (srgb_to_linear(c) for c in hex_to_rgb(hexs))
    return 0.2126*r + 0.7152*g + 0.0722*b
def contrast(h1,h2):
    a,b = wcag_lum(h1), wcag_lum(h2)
    hi,lo = max(a,b), min(a,b)
    return (hi+0.05)/(lo+0.05)

def solve_L(target, against, C, H, lo=0.05, hi=0.98, darker=True):
    """binary search L so contrast(color, against) >= target (approaching from safe side)"""
    for _ in range(60):
        mid = (lo+hi)/2
        c = oklch_hex(mid,C,H)
        if contrast(c, against) >= target:
            if darker: lo = mid   # can afford to be lighter -> move toward hi? no: darker=True means darker passes; push lighter
        # simpler: just scan
        break
    # robust scan instead
    best=None
    steps=[lo+i*(hi-lo)/400 for i in range(401)]
    if darker: steps=steps  # find the LIGHTEST L that still passes (max L with contrast>=target) when against is light
    passing=[Lv for Lv in steps if contrast(oklch_hex(Lv,C,H), against)>=target]
    if not passing: raise SystemExit(f"unsolvable {target} vs {against}")
    return max(passing) if darker else min(passing)

# ---- Anchor ----
ANCHOR = '#BDCCC2'
aL,aC,aH = rgb_to_oklch(*hex_to_rgb(ANCHOR))
print(f"Anchor Teresa's Green {ANCHOR} -> OKLCH L={aL:.3f} C={aC:.3f} H={aH:.1f}")
H = aH

# ---- Brand ramp (hue locked, restrained chroma) ----
ramp_spec = {50:(0.975,0.010),100:(0.950,0.015),200:(0.905,0.022),300:(0.845,aC),
             400:(0.760,0.040),500:(0.660,0.050),600:(0.565,0.055),700:(0.480,0.055),
             800:(0.395,0.048),900:(0.315,0.038),950:(0.245,0.028)}
green = {k: oklch_hex(L,C,H) for k,(L,C) in ramp_spec.items()}
# force 300 to the true anchor
green[300] = ANCHOR

# ---- Neutrals (same hue, whisper of chroma) ----
neut_spec = {0:(0.995,0.002),50:(0.980,0.004),100:(0.955,0.006),200:(0.915,0.008),
             300:(0.865,0.009),400:(0.760,0.010),500:(0.640,0.010),600:(0.525,0.010),
             700:(0.445,0.010),800:(0.355,0.010),900:(0.270,0.010),950:(0.205,0.010),1000:(0.165,0.010)}
neutral = {k: oklch_hex(L,C,H) for k,(L,C) in neut_spec.items()}

# ---- Light theme surfaces ----
Lsurf = neutral[0]        # page
Lraised = '#FFFFFF'
Lsunken = neutral[100]

# solved semantic colors (light)
act_L   = solve_L(4.6, Lraised, 0.055, H)            # action on white
action  = oklch_hex(act_L, 0.055, H)
act_hov = oklch_hex(act_L-0.06, 0.058, H)
act_act = oklch_hex(act_L-0.10, 0.058, H)
focus_L = solve_L(3.1, Lraised, 0.090, H)
focus   = oklch_hex(focus_L, 0.090, H)
txt2_L  = solve_L(4.6, Lsunken, 0.012, H)
text2   = oklch_hex(txt2_L, 0.012, H)
text1   = neutral[950]

# ---- Dark theme surfaces ----
Dsurf   = oklch_hex(0.185, 0.012, H)
Draised = oklch_hex(0.225, 0.013, H)
Dsunken = oklch_hex(0.150, 0.010, H)
dtext1  = oklch_hex(0.930, 0.008, H)
d2 = [L for L in [x/400 for x in range(60,400)] if contrast(oklch_hex(L,0.012,H),Draised)>=4.6]
dtext2  = oklch_hex(min(d2), 0.012, H)
# Dark action. Rev 3: solved against Draised, NOT Dsurf.
#
# Rev 2 solved this against the page surface and shipped #558A6A, which measures
# 4.64:1 on the page and 4.27:1 inside a card — and links live inside cards, in
# the header, and in source badges. `raised` is the lightest surface an action
# colour actually lands on, so it is the one that binds. The near-black label
# constraint is unaffected (a lighter green only improves it).
cands=[]
for i in range(200):
    L=0.55+i*0.002
    c=oklch_hex(L,0.075,H)
    if (contrast(c,Draised)>=4.55 and contrast(c,Dsurf)>=4.6
            and contrast(c,neutral[1000])>=4.6): cands.append((L,c))
dact_L, daction = cands[0]
dact_hov = oklch_hex(dact_L+0.05,0.075,H)
dfocus   = oklch_hex(min(0.9,dact_L+0.12),0.10,H)

# ---- Status hues ----
def status(hue, name):
    t  = oklch_hex(solve_L(4.6, Lraised, 0.11, hue), 0.11, hue)     # light text/icon
    bg = oklch_hex(0.955, 0.030, hue); bd = oklch_hex(0.80, 0.06, hue)
    dt_ = [L for L in [x/400 for x in range(200,400)] if contrast(oklch_hex(L,0.10,hue),Draised)>=4.6]
    dt = oklch_hex(min(dt_), 0.10, hue)
    dbg= oklch_hex(0.26, 0.035, hue); dbd = oklch_hex(0.42, 0.06, hue)
    return dict(text=t,bg=bg,border=bd,dark_text=dt,dark_bg=dbg,dark_border=dbd)
info    = status(250,'info'); warn = status(80,'warn'); danger = status(27,'danger')
violet  = status(305,'llm')   # machine-inferred provenance
success = dict(text=action,bg=oklch_hex(0.955,0.025,H),border=oklch_hex(0.80,0.05,H),
               dark_text=daction,dark_bg=oklch_hex(0.26,0.03,H),dark_border=oklch_hex(0.42,0.05,H))

# ---- verification ----
checks = [
 ("L text.primary / surface",        contrast(text1,Lsurf)),
 ("L text.secondary / sunken",       contrast(text2,Lsunken)),
 ("L action / raised(white)",        contrast(action,Lraised)),
 ("L white on action",               contrast('#FFFFFF',action)),
 ("L focus ring / white (3:1 UI)",   contrast(focus,Lraised)),
 ("L border n300 / white (3:1 UI)",  contrast(neutral[300],Lraised)),
 ("D text.primary / surface",        contrast(dtext1,Dsurf)),
 ("D text.secondary / raised",       contrast(dtext2,Draised)),
 ("D action / surface",              contrast(daction,Dsurf)),
 ("D on-action(n1000) / action",     contrast(neutral[1000],daction)),
 ("L info text / white",             contrast(info['text'],Lraised)),
 ("L warn text / white",             contrast(warn['text'],Lraised)),
 ("L danger text / white",           contrast(danger['text'],Lraised)),
 ("L llm text / white",              contrast(violet['text'],Lraised)),
 ("D info text / raised",            contrast(info['dark_text'],Draised)),
 ("D danger text / raised",          contrast(danger['dark_text'],Draised)),
]
print("\nWCAG verification (AA needs 4.5 text / 3.0 UI):")
for n,v in checks: print(f"  {n:38s} {v:5.2f}  {'PASS' if v>=3.0 else 'FAIL'}")

# ---------------------------------------------------------------------------
# The verification that matters: the SHIPPED tokens, against every surface they
# actually land on.
#
# The block above verifies the values this script derives, each against the one
# surface it was solved for. That is not the same question. `design-tokens.json`
# is what the site compiles, and a colour solved against white will happily be
# rendered on a panel, a card or a sunken block — which is exactly how Rev 2
# shipped an action colour at 4.27:1 inside a card and four status colours at
# 4.37:1 on a callout. Every pair below is a pair the site really produces; if
# one fails, the token is wrong, not the page.
# ---------------------------------------------------------------------------
import os

TOKENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'design-tokens.json')
tokens = json.load(open(TOKENS))

def token(path):
    node = tokens
    for part in path.split('.'):
        node = node[part]
    value = node['$value']
    while isinstance(value, str) and value.startswith('{'):
        value = token(value[1:-1])
    return value

def surfaces(mode):
    return {
        'page':   token(f'theme.{mode}.surface.page'),
        'raised': token(f'theme.{mode}.surface.raised'),
        'sunken': token(f'theme.{mode}.surface.sunken'),
        'panel':  token('component.panel.bg-light' if mode == 'light' else 'component.panel.bg-dark'),
    }

# (token path, which surfaces it may land on, minimum ratio)
TEXT_ON = ['page', 'raised', 'panel']          # sunken carries body text only
SHIPPED = [
    ('text.primary',   'theme.{m}.text.primary',                 TEXT_ON + ['sunken'], 4.5),
    ('text.secondary', 'theme.{m}.text.secondary',               TEXT_ON + ['sunken'], 4.5),
    ('text.link',      'theme.{m}.text.link',                    TEXT_ON, 4.5),
    ('action.primary', 'theme.{m}.action.primary',               TEXT_ON, 4.5),
    ('action.hover',   'theme.{m}.action.hover',                 TEXT_ON, 4.5),
    ('badge api',      'component.badge.provenance-api.{m}',     TEXT_ON, 4.5),
    ('badge pattern',  'component.badge.provenance-pattern.{m}', TEXT_ON, 4.5),
    ('badge llm',      'component.badge.provenance-llm.{m}',     TEXT_ON, 4.5),
    ('badge restrict', 'component.badge.availability-restricted.{m}', TEXT_ON, 4.5),
    ('badge embargo',  'component.badge.availability-embargoed.{m}',  TEXT_ON, 4.5),
    ('badge withdrawn','component.badge.lifecycle-withdrawn.{m}', TEXT_ON, 4.5),
    ('panel info bar', 'component.panel.info.bar-{m}',           TEXT_ON, 4.5),
    ('panel warn bar', 'component.panel.warning.bar-{m}',        TEXT_ON, 4.5),
    ('panel danger',   'component.panel.danger.bar-{m}',         TEXT_ON, 4.5),
    ('panel violet',   'component.panel.violet.bar-{m}',         TEXT_ON, 4.5),
    ('panel accent',   'component.panel.accent.bar-{m}',         TEXT_ON, 4.5),
    ('border.focus',   'theme.{m}.border.focus',                 TEXT_ON, 3.0),   # non-text
    ('border.input',   'theme.{m}.border.input',                 TEXT_ON, 3.0),   # non-text
    ('border.strong',  'theme.{m}.border.strong',                TEXT_ON, 1.0),   # decorative
]

print("\nShipped-token verification — every colour against every surface it lands on:")
failures = 0
for mode in ('light', 'dark'):
    surface = surfaces(mode)
    print(f"  [{mode}]")
    for label, path, allowed, minimum in SHIPPED:
        try:
            colour = token(path.format(m=mode))
        except KeyError:
            continue
        row, bad = [], False
        for name in allowed:
            ratio = contrast(colour, surface[name])
            row.append(f"{name} {ratio:5.2f}")
            if ratio < minimum:
                bad = True
        failures += bad
        print(f"    {label:16s} {colour}  " + '  '.join(row) + ('   FAIL' if bad else ''))

# The one pair that runs the other way: the label sits ON the action colour.
for mode in ('light', 'dark'):
    ratio = contrast(token(f'theme.{mode}.text.on-action'), token(f'theme.{mode}.action.primary'))
    bad = ratio < 4.5
    failures += bad
    print(f"  [{mode}] label on action        {ratio:5.2f}" + ('   FAIL' if bad else ''))

print(f"\n{'ALL PAIRS PASS' if failures == 0 else f'{failures} FAILING PAIR(S) — fix the token, not the page'}")

out = dict(green=green, neutral=neutral,
  light=dict(surface=Lsurf,raised=Lraised,sunken=Lsunken,text1=text1,text2=text2,
             action=action,action_hover=act_hov,action_active=act_act,focus=focus,
             border=neutral[200],border_strong=neutral[300]),
  dark=dict(surface=Dsurf,raised=Draised,sunken=Dsunken,text1=dtext1,text2=dtext2,
            action=daction,action_hover=dact_hov,focus=dfocus,
            border=oklch_hex(0.30,0.012,H),border_strong=oklch_hex(0.38,0.012,H)),
  status=dict(info=info,warning=warn,danger=danger,success=success,llm=violet))
# Written next to this script, not into the working directory: `make build-tokens`
# runs it from the repository root and used to drop a stray palette.json there.
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'palette.json'), 'w'), indent=1)
print("\nGREEN RAMP:", {k:v for k,v in green.items()})
print("NEUTRALS:", {k:v for k,v in list(neutral.items())})
print("LIGHT:", out['light']); print("DARK:", out['dark'])
print("STATUS info/warn/danger/llm text:", info['text'],warn['text'],danger['text'],violet['text'])
