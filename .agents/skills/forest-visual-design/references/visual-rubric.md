# Visual system and rendered review

The source snapshot is `ff16c3f656f94d053d1bba65c6b3a07a5feaccde` (2026-10-13). It already has `styles.css` tokens, dark-theme rules, Lucide icons, `run-evidence.css`, and `progress/progress.css`. `ui.tsx` implements topmost modal Escape handling, focus trapping/restoration, and keyboard tabs; corresponding `Modal.test.tsx` and `Tabs.test.tsx` protect behavior. Inspect current ownership before extending.

`styles.css` imports DM Sans/Manrope remotely. Test actual availability and fallback metrics rather than assuming those fonts loaded. Many rules use 7–11 px text and multiple media-query overrides; determine which are functional labels through browser inspection before changing the scale. Source findings do not establish rendered contrast or usability.

## Initial tokens to test, not final measured values

| Role | Light starting value | Dark starting value |
| --- | --- | --- |
| App background | `#F4F6F2` | `#101715` |
| Surface | `#FFFFFF` | `#19221F` |
| Elevated surface | `#FAFBF8` | `#222D28` |
| Primary text | `#18211D` | `#EDF2EE` |
| Secondary text | `#59675E` | `#A5B0A9` |
| Accent | `#2E6549` | `#8CB69B` |
| Accent hover | `#234C39` | `#A4CBB0` |
| Border | `rgba(30,55,39,0.10)` | `rgba(255,255,255,0.09)` |
| Paper body | `#FAF8F3` | Select deliberately for readable paper preview |

Use separate semantic status roles. Never equate accent green with scientific verification. These values come from the requested direction and need rendered contrast tests, including overlay compositing, focus, and disabled states.

Suggested scale: title 24–28 px; section 17–20; body 14; secondary 13; small labels 12. Use suitable system fallback, code/log monospace, formula rendering, and manuscript typography. Spacing can follow 4/8/12/16/20/24/32/40; buttons/inputs approximately 8–12 px radius and floating surfaces 14–18. Adjust for actual content rather than enforcing every suggested dimension.

Suggested shell: expanded sidebar 216–232 px, collapsed 56–68; topbar 52–60 px; status bar 32–36. Graph nodes approximately 220–260 px where titles fit. These are starting constraints, not reasons to truncate content or disable touch access.

For appropriate chrome, trial `backdrop-filter: blur(16px) saturate(135%)` with opaque fallback, avoiding nested glass. Trial 120–200 ms feedback and 180–260 ms panel changes with reduced-motion alternatives. Measure graph and scrolling cost before keeping blur or motion effects.

## Screenshot critique record

| Criterion | Record |
| --- | --- |
| Primary task and action | Clearly identified content/action and any competing emphasis |
| Panel proportions | Viewport and usable main width; overlapping or redundant surfaces |
| Typography | Critical label/body sizes, fallback font, Chinese/English wrapping |
| Space and alignment | Specific cramped/gapped/misaligned locations |
| Surfaces and depth | Redundant borders, card nesting, glass stacking, readable background |
| Theme and status | Text/focus contrast evidence; status meaning independent of color |
| Responsive access | How formerly hidden actions remain reachable at each tested width |
| Interaction | Keyboard focus, hit area, modal close/restoration, drawer access |
| Iteration | Before image → observed defect → code change → after image |

Store browser screenshots and traces with page, theme, viewport, state, and tested source revision. Do not overwrite baseline evidence with new screenshots. If a run/decision/error is seeded or routed in a fixture test, mark it as such.

## Accessibility evidence

Reference [WCAG 2.2](https://www.w3.org/TR/WCAG22/) when applying thresholds: normal text generally 4.5:1; qualifying large text 3:1; required visual controls/state indicators generally 3:1. Review relevant focus visibility, keyboard, reflow, status announcement, and minimum target-size criteria with their exceptions rather than treating color ratios as the whole standard.

Use explicit reduced-transparency application preference if platform media-query support is uncertain; test both paths. At 200% zoom, verify dialogs, navigation, result tables, editors, and all frequent actions still function. Do not infer screen-reader behavior from DOM labels alone when claiming it was tested.
