---
name: Kinetic Intelligence
colors:
  surface: '#0b1326'
  surface-dim: '#0b1326'
  surface-bright: '#31394d'
  surface-container-lowest: '#060e20'
  surface-container-low: '#131b2e'
  surface-container: '#171f33'
  surface-container-high: '#222a3d'
  surface-container-highest: '#2d3449'
  on-surface: '#dae2fd'
  on-surface-variant: '#bbcabe'
  inverse-surface: '#dae2fd'
  inverse-on-surface: '#283044'
  outline: '#859489'
  outline-variant: '#3c4a41'
  surface-tint: '#42e09a'
  primary: '#61f9b1'
  on-primary: '#003822'
  primary-container: '#3ddc97'
  on-primary-container: '#005c3a'
  inverse-primary: '#006c45'
  secondary: '#bfc7d8'
  on-secondary: '#29313e'
  secondary-container: '#414958'
  on-secondary-container: '#b1b9ca'
  tertiary: '#d5dff5'
  on-tertiary: '#273141'
  tertiary-container: '#b9c3d9'
  on-tertiary-container: '#465062'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#65fdb5'
  primary-fixed-dim: '#42e09a'
  on-primary-fixed: '#002112'
  on-primary-fixed-variant: '#005233'
  secondary-fixed: '#dbe3f5'
  secondary-fixed-dim: '#bfc7d8'
  on-secondary-fixed: '#141c29'
  on-secondary-fixed-variant: '#3f4755'
  tertiary-fixed: '#d9e3f9'
  tertiary-fixed-dim: '#bdc7dc'
  on-tertiary-fixed: '#121c2c'
  on-tertiary-fixed-variant: '#3d4759'
  background: '#0b1326'
  on-background: '#dae2fd'
  surface-variant: '#2d3449'
typography:
  display-lg:
    fontFamily: Hanken Grotesk
    fontSize: 48px
    fontWeight: '700'
    lineHeight: 56px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Hanken Grotesk
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: -0.01em
  headline-lg-mobile:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Hanken Grotesk
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  body-lg:
    fontFamily: Hanken Grotesk
    fontSize: 18px
    fontWeight: '400'
    lineHeight: 28px
  body-md:
    fontFamily: Hanken Grotesk
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-md:
    fontFamily: JetBrains Mono
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.02em
  label-sm:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  base: 8px
  container-padding: 24px
  gutter: 20px
  sidebar-width: 260px
  stack-gap-sm: 8px
  stack-gap-md: 16px
  stack-gap-lg: 32px
---

## Brand & Style

The design system is engineered for educational institutions requiring precision, speed, and reliability. The brand personality is **Technical, Authoritative, and Frictionless**. It avoids the "academic" dryness of traditional software, instead adopting a **High-Tech SaaS** aesthetic that feels like a mission control center for campus operations.

The visual style is a hybrid of **Modern Corporate** and **Soft Glassmorphism**. It utilizes deep, atmospheric backgrounds to reduce eye strain during long administrative sessions, punctuated by high-vibrancy status indicators that draw immediate attention to critical data. The emotional response should be one of total control and effortless efficiency.

## Colors

The palette is anchored in a **Deep Navy and Charcoal** foundation to establish a professional, high-tech environment. 

- **Primary (#3DDC97):** A vibrant Mint Green used exclusively for primary actions, success states, and "Live" indicators. It must provide high contrast against the dark background.
- **Surface Tiers:** Use `#0F172A` for the base background. Use `#1A222F` for primary containers (sidebar, cards) and `#2D3748` for elevated elements like inputs and hover states.
- **Accents:** Use Mint Green sparingly for data visualizations and active navigational markers to maintain its impact.
- **Status Colors:** Standardized semantic colors for "Pending" (Amber), "Idle" (Slate), and "Error" (Red) ensure immediate cognitive recognition of system states.

## Typography

This design system utilizes **Hanken Grotesk** for all primary interface elements, providing a sharp, contemporary sans-serif feel that scales perfectly from dense data tables to large dashboard headers. 

To reinforce the "Smart System" technicality, **JetBrains Mono** is utilized for labels, status badges, and numerical data. This monospaced secondary font suggests precision and programmatic accuracy. 

- **Weight Usage:** Use Bold (700) only for display; Semi-Bold (600) for headlines; Regular (400) for all body text and descriptions.
- **Letter Spacing:** Apply slight negative tracking to large headlines to keep them tight and impactful.

## Layout & Spacing

The layout follows a **Fixed-Fluid Hybrid** model. A fixed-width sidebar (260px) persists on the left, while the main content area utilizes a fluid 12-column grid.

- **Desktop:** 12 columns, 24px margins, 20px gutters.
- **Tablet:** 8 columns, 16px margins, 16px gutters.
- **Mobile:** 4 columns, 16px margins, 12px gutters.

The spacing rhythm is strictly base-8. Use `stack-gap-md` (16px) for most vertical relationships between components, and `stack-gap-lg` (32px) for separating major sections within a dashboard view.

## Elevation & Depth

Hierarchy is established through **Tonal Layering** and **Glassmorphism** rather than traditional heavy shadows.

1.  **Level 0 (Base):** Deep Navy (#0F172A). No effects.
2.  **Level 1 (Cards/Sidebar):** Charcoal (#1A222F). Subtle 1px border (#ffffff 10% opacity).
3.  **Level 2 (Modals/Popovers):** Surface at #2D3748 with a **Backdrop Blur** of 12px and 40% opacity. This creates the "glass" effect against the vibrant background data or charts.
4.  **Interactive Elements:** Buttons and active inputs should have a subtle outer glow (0px 0px 8px) using the Primary Mint color at 20% opacity when focused or hovered.

Avoid drop shadows except for floating action buttons, which use a high-spread, low-opacity (#000000 40%) shadow to lift them from the glass layers.

## Shapes

The design system uses a **Soft (0.25rem)** roundedness approach to maintain a "crisp" and professional technical aesthetic. 

- **Standard Elements:** Inputs, small buttons, and list items use 4px (0.25rem) corners.
- **Large Elements:** Dashboard cards and modals use `rounded-lg` (8px / 0.5rem) to slightly soften the structure without appearing "bubbly."
- **Badges:** Status badges use a 2px radius or remain nearly square to emphasize the "technical/data" nature of the attendance system.

## Components

### Buttons & Inputs
- **Primary Button:** Solid Mint Green (#3DDC97) with Navy text. High-contrast, no gradient.
- **Secondary Button:** Outline only (1px Mint) with Mint text.
- **Input Fields:** Dark fill (#2D3748) with a 1px border (#3DDC97) appearing only on focus. Use JetBrains Mono for placeholder text.

### Status Badges
- **Complete:** Mint background, 10% opacity, Solid Mint 1px border, Mint text.
- **Live:** Solid Mint background with a small white pulsing dot.
- **Pending:** Amber background, 10% opacity, Solid Amber 1px border.
- **Idle:** Slate background, 10% opacity, 1px Slate border.

### Cards
- Cards must use the Glassmorphism style: semi-transparent charcoal background with a 1px "inner-light" border at the top to simulate a glass edge.

### Attendance List
- Use Zebra-striping with #1A222F and #1E293B. 
- Active rows (e.g., currently being scanned) should have a 2px left-border accent of Mint Green.

### Data Visualization
- Graphs should use the Primary Mint for "Success/Present" data and a subtle white/grey for "Total/Denominator" data. Ensure all lines have a slight glow effect (1px-2px blur) to lean into the high-tech dashboard aesthetic.