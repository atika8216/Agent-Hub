# Design Tokens

All colors use OKLCH for perceptual uniformity. Neutrals are **cool-tinted toward indigo**
(~250deg hue) to create visual tension with the hot Databricks red-orange accent.

## Colors -- Primary

| Token | OKLCH | Hex (fallback) | Usage |
|---|---|---|---|
| Primary | `oklch(0.63 0.26 29)` | #ff3621 | Databricks red-orange accent, CTAs |
| Primary foreground | `oklch(1.0 0 0)` | #ffffff | Text on primary backgrounds |

## Colors -- Surfaces (cool indigo-tinted neutrals)

| Token | OKLCH | Hex (approx) | Usage |
|---|---|---|---|
| Background | `oklch(0.08 0.008 250)` | #0e1017 | Page background (deep indigo-black) |
| Surface-elevated | `oklch(0.11 0.008 250)` | #141820 | Cards, sidebar, elevated containers |
| Surface-overlay | `oklch(0.14 0.009 250)` | #1a1f2a | Popovers, overlays, modals |
| Border | `oklch(0.16 0.010 250)` | #1e2433 | 1px borders on cards/inputs |
| Surface-recessed | `oklch(0.06 0.007 250)` | #0a0c12 | User message blocks (chat) |
| Text-primary | `oklch(0.93 0.005 250)` | #e8eaf0 | Primary text on dark surfaces |
| Text-secondary | `oklch(0.62 0.008 250)` | #8b90a0 | Secondary text, descriptions |
| Text-muted | `oklch(0.42 0.008 250)` | #555b6e | Timestamps, tertiary labels |

## Colors -- Semantic

| Token | OKLCH | Hex (approx) | Usage |
|---|---|---|---|
| Success | `oklch(0.72 0.19 145)` | #3dcc62 | Access granted, connected status |
| Error | `oklch(0.60 0.22 25)` | #ef4444 | No access, errors, disconnected |
| Warning | `oklch(0.78 0.15 85)` | #f59e0b | Pending access, degraded status |
| Info | `oklch(0.65 0.16 250)` | #4e8be0 | Links, secondary actions, info badges |

## Colors -- Type Badges (sub-agent types)

Badge style: 10% tinted background with full-intensity text (cockpit-display readout).

| Type | OKLCH | Hex (approx) | Usage |
|---|---|---|---|
| MAS | `oklch(0.63 0.26 29)` | #ff3621 | Red-orange badge |
| Genie | `oklch(0.65 0.16 250)` | #4e8be0 | Blue badge |
| KA | `oklch(0.58 0.22 300)` | #a855f7 | Purple badge |
| UC Function | `oklch(0.78 0.15 85)` | #f59e0b | Amber badge |
| External MCP | `oklch(0.68 0.14 175)` | #14b8a6 | Teal badge |

## Typography

| Role | Font (Implementation) | Font (Stitch Proxy) | Weight | Source |
|---|---|---|---|---|
| Headlines | Satoshi | Sora | 600-700 | Fontshare CDN |
| Body | Switzer | DM Sans | 400-500 | Fontshare CDN |
| Labels | Satoshi | Sora | 400-500 | Fontshare CDN |
| Monospace | JetBrains Mono | -- | 400-500 | Google Fonts / bundled |

**Type scale**: 14px base, 1.25 ratio (11, 12, 13, 14, 16, 18, 22, 28, 36, 48)

**Typography fine-tuning**:
- Headlines: tight letter-spacing (-0.02em), line-height 1.2-1.35
- Body: generous line-height (1.6) for readability on dark backgrounds
- Labels: slightly tracked (+0.02-0.04em), uppercase for category labels

**Stitch proxy note**: Stitch tends to override specified fonts to Space Grotesk / Inter.
The mockups are layout/concept references; actual fonts come from Fontshare CDN.

## Spacing

- 4pt base scale: 4, 8, 12, 16, 24, 32, 48, 64
- Card padding: 16px
- Grid gap: 16px
- Section gap: 32px
- Sidebar width: 56px (nav rail), 240px (session sidebar), 280px (context panel)
- Spacing scale: compact (1) -- information density over whitespace

## Border Radius

- Small: 4px (badges, buttons, inputs) -- ROUND_FOUR in Stitch
- Medium: 4px (cards) -- precision instruments have tight radii
- Large: 8px (modals only)

## Shadows

- No heavy drop shadows. Depth via surface color steps only.
- Exception: popovers/dropdowns use `0 4px 16px oklch(0 0 0 / 0.4)`

## Access Indicators

- Connected/Granted: Success green LED dot (#3dcc62) + "Connected" / "Access" label
- Pending: Warning amber LED dot (#f59e0b) + "Pending Access" label
- No Access: Error red LED dot (#ef4444) + "No Access" label
- Loading: Muted text color pulsing skeleton with indigo pulse animation

## Interaction States

- Hover: subtle surface brightness increase (+1 luminance step)
- Active/Selected: 2px left accent border (#ff3621) + slightly brighter surface
- Focus: thin ring in info blue (#4e8be0)
- Disabled: 40% opacity, no other visual change

## Stitch Project Reference (v2 -- Observatory)

- Project: "SCGP Agent Hub v2 -- Observatory"
- Project ID: `10483141641832095383`
- Base Design System Asset: `assets/15441572755616268220` ("The Observatory")

### Screen Design Systems (auto-evolved by Stitch per screen)

| Screen | Design System | Asset ID |
|---|---|---|
| Agent Catalog (The Index) | The Observatory | `assets/15441572755616268220` |
| Agent Detail (The Dossier) | The Observatory | `assets/15441572755616268220` |
| Chat Interface (The Channel) | Mission Control | `assets/1fd98133bfec4aba96ecee64b27040e4` |
| Admin Catalog Mgmt (The Registry) | The Registry | `assets/20e0973394324ce0830332b4fc233cc9` |
| Admin Settings (The Configuration) | Tactical Vanguard | `assets/1b3ea47899444a0287dde5c2d4003ff7` |
| Empty/Edge States (The Void) | Event Horizon | `assets/f0c3125b2f0344ff949787aa4aa4e563` |

### Screen IDs

| Screen | Screen ID |
|---|---|
| Agent Catalog (The Index) -- v1 (Metropolis) | `3069a275b4e24fdf8574cff0ba72819e` |
| Agent Catalog (The Index) -- v2 (Sora) | `2dfa9e5126944f6293b3dec6714118e7` |
| Agent Detail (The Dossier) | `146bd709780241b59eb651cf9fa5fc29` |
| Chat Interface (The Channel) | `27bed06ee7d54eb69cbf453ccfa496b4` |
| Admin Catalog Mgmt (The Registry) | `752b11bee55e4c2f8db21473eabc594b` |
| Admin Settings (The Configuration) | `523d292f857046f0ab4cf04cddca92bd` |
| Empty/Edge States (The Void) | `7603f46bfb9a4813b0405d4170431796` |

### Previous Stitch Projects (deprecated)

- v1 Project ID: `17674224124953744369` (warm-tinted "Forge" concept -- replaced)

All screens use cool indigo-black backgrounds (~#0e1017), Space Grotesk/Inter as
Stitch-chosen rendering fonts (overriding Sora/DM Sans specification). The actual
implementation will use Satoshi + Switzer from Fontshare CDN.
