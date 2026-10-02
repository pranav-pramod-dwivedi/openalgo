# PranavPay — Product & Design Plan

## Scope

A responsive redesign of PranavPay as a premium trading wallet control center. AI executes trades; this UI gives the owner a calm, readable way to understand the portfolio and control the agent. The experience is a frontend prototype with realistic sample data and interactive controls.

## Design direction

- **Design movement:** Apple-inspired editorial minimalism with a monochrome financial-instrument sensibility.
- **Core principles:** (1) quiet confidence over dashboard noise, (2) hierarchy through space and type rather than color, (3) every action has a legible state, (4) dense data is staged into calm reading zones.
- **Color philosophy:** strict black, white, and neutral grayscale only. Black is the decision and signal color; white is the breathing room; gray is reserved for supporting context. Gains and losses do not use green/red, preserving a focused, non-gamified control-room feeling.
- **Layout paradigm:** a persistent left command rail anchors the product; the main canvas uses asymmetrical editorial blocks with one wide performance surface, a narrow AI state column, and a long position ledger beneath rather than a centered card grid.
- **Signature elements:** a custom geometric P mark in a rounded square; hairline rules that act like a financial notebook grid; a black orbit/status module that makes the AI agent feel present but controlled.
- **Interaction philosophy:** calm, immediate, reversible. Navigation, chart ranges, and AI controls respond in-place with short feedback messages rather than disruptive page changes.
- **Animation:** use short 180–260ms cubic-bezier transitions for controls and hover states; chart lines reveal with a gentle stroke-dash effect; the AI orbit rotates slowly only while active; avoid bouncing or attention-seeking motion.
- **Typography system:** system-ui / -apple-system stack with tight display tracking for large balances, medium-weight labels, and tabular-nums for all money and time series values. Use uppercase micro-labels sparingly with wide letter spacing.
- **Brand essence:** The quiet control surface for autonomous capital — precise, composed, and human-readable. Personality: **composed, intelligent, exacting**.
- **Brand voice:** short, direct, reassuring. Example lines: “The system is watching the market.” and “Pause the agent without closing your positions.”
- **Wordmark & logo:** “PranavPay” wordmark paired with a rounded-square P made from two offset black strokes, suggesting a wallet fold and a live trading loop.
- **Signature brand color:** none by intention; PranavPay owns a pure monochrome system instead of an accent hue.

## Product structure

- `index.html`: semantic dashboard shell, navigation, portfolio summary, chart, AI controls, position ledger, activity feed, and responsive mobile navigation.
- `styles.css`: monochrome design tokens, responsive layout, typography, component states, chart surfaces, and motion.
- `app.js`: local sample data, chart range switching, navigation state, AI pause/stop transitions, toasts, and lightweight modal-like feedback.
- `public/manus-routes.json`: route manifest for the dashboard page.
- `server.js`: minimal static server on the configured port for preview.
- `package.json`: start script and local runtime metadata.

## Delivery constraints

- No login, billing, backend, or external image assets are required for this internal dashboard prototype.
- The application listens on `0.0.0.0:3000` and serves a valid `/manus-routes.json` file before SPA fallback.
