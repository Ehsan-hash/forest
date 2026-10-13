# FOREST design system

The running application uses `apps/web/src/design-system.css`, loaded after the existing feature styles, as the semantic theme and layout layer. Existing feature components remain the implementation owners. It uses system fonts, Lucide icons and the shared Button, IconButton, Tabs, Badge, ErrorBox and Modal components.

| Role | Light | Dark |
| --- | --- | --- |
| Background | `#f4f6f2` | `#101715` |
| Reading surface | `#ffffff` | `#19221f` |
| Elevated surface | `#fafbf8` | `#222d28` |
| Main text | `#18211d` | `#edf2ee` |
| Secondary text | `#59675e` | `#b0bcb4` |
| Accent/action | `#2e6549` | `#8cb69b` |
| Selected surface | `#e4eee6` | `#293f31` |
| Separator | `#d5dfd6` | `#3e5046` |
| Control boundary | `#76877b` | `#7d9687` |
| Focus | `#276345` | `#a4cbb0` |
| Manuscript page | `#faf8f3` | `#faf8f3` |

Light, Dark and System are saved local preferences. System follows actual media changes. Reduce transparency removes backdrop blur and supplies opaque chrome. Reduced motion disables unnecessary transitions. Navigation and transient chrome may use a single glass layer; graph cards, code, terminal output, tables, figures and manuscript reading surfaces remain opaque.

Body and inputs use 14px; primary headings use 26px, secondary headings 19px, and node titles 16px. Metadata is generally 12–13px. Buttons and icon buttons normally have a 36px minimum height; sidebar destinations have at least 42px. Focus-visible uses a 2px semantic outline with a 3px offset. Execution, evidence and errors have text labels as well as color.

The expanded sidebar is 216px and the collapsed desktop sidebar is 68px. At compact widths it becomes a drawer with an explicit trigger and scrim; global Settings stays reachable. Secondary navigation and action groups wrap or scroll internally. Graph search remains visible. Node inspectors respond to live viewport changes. The manuscript panel has minimum usable editor/preview heights and a resize separator; compact mode traps focus and returns it to the graph on dismissal.

Modal content is portaled to the document body so a frosted, clipped sidebar cannot contain its fixed overlay. Modal preserves focus entry, Tab containment, topmost Escape dismissal and trigger restoration; static rendering retains dialog semantics without a browser document. ErrorBox is an alert with a durable retry entry where supported.

Rendered review found and corrected low-contrast legacy metadata, disappearing narrow manuscript controls, an intrinsic grid minimum that overflowed Overview at 1280px, and a desktop manuscript side-panel editor with collapsed height. Final measured contrast and responsive observations belong to [visual-comparison.md](visual-comparison.md) and [test-matrix.md](test-matrix.md). This specification does not claim complete WCAG conformance.
