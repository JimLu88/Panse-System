---
name: panse-design
description: Use this skill to generate well-branded interfaces and assets for 畔色孚格 ERP (Panse ERP), either for production or throwaway prototypes/mocks/etc. Contains essential design guidelines, colors, type, fonts, assets, and UI kit components for prototyping a furniture e-commerce internal ERP with strong readability and web + mobile adaptation.
user-invocable: true
---

Read the README.md file within this skill, and explore the other available files.

If creating visual artifacts (slides, mocks, throwaway prototypes, etc), copy assets out and create static HTML files for the user to view. If working on production code, you can copy assets and read the rules here to become an expert in designing with this brand.

If the user invokes this skill without any other guidance, ask them what they want to build or design, ask some questions, and act as an expert designer who outputs HTML artifacts _or_ production code, depending on the need.

## Quick reference
- **Brand**: 畔色孚格 ERP — furniture e-commerce internal ERP. Vibe: trustworthy "digital steward" — calm, precise, readable, never flashy.
- **Primary color**: Google blue `--blue-600 #1a73e8` (brand `--blue-500 #4285f4`, deep nav `--blue-900 #174ea6`); neutrals = slate; status = emerald/amber/rose/sky.
- **Type**: Noto Sans SC (UI) + JetBrains Mono (money/IDs, tabular, right-aligned). Body 15px, table 14px.
- **Surfaces**: white cards, radius 16, 1px border, soft `--shadow-xs`; app bg `#f8fafc`. Dark mode via `[data-theme="dark"]` (deep blue-grey).
- **Stack mirrored**: Ant Design v5 + Material Symbols (Outlined) icons + ECharts. Top deep-blue nav + grouped menu + content padding 24.
- Foundations in `guidelines/`, primitives in `components/`, full screens in `ui_kits/` (web + mobile).
- Link `styles.css` for tokens; components live on `window.ERPDesignSystem_dc7e11` via `_ds_bundle.js`.

## Rules of thumb
- Readability first: dense data, right-aligned mono numbers, three table densities (40/48/56), sticky headers, hover-revealed actions.
- Web AND mobile must both be handled; mobile tap targets ≥ 44px, table → card/list on small screens.
- Status = soft-tint tag (fill + same-color text + thin border), radius 8. Money always `¥` + thousands separators.
- Animate from hidden via transform (never rest at opacity:0); honor prefers-reduced-motion. Use ECharts SVG renderer.
- Don't introduce new hues; use the blue/slate/semantic tokens. Icons = Material Symbols Outlined; no emoji in formal UI.
