# Iron Plus Gym

A starter structure for Iron Plus Gym member management with a clean frontend and backend scaffold.

## Project structure

- `index.html` — main app shell served from the project root
- `src/styles/main.css` — shared styling for the frontend
- `src/scripts/app.js` — frontend behavior and data interactions
- `backend/app.js` — Express backend entry point
- `backend/routes/` — API route modules
- `backend/db/` — SQLite database files and schema

## Getting started

1. Install dependencies:

```bash
npm install
```

2. Start the backend server:

```bash
npm run dev
```

3. Open `http://localhost:4000` in your browser.

## Notes

- The backend uses SQLite for a lightweight database that is easy to grow.
- `backend/db/schema.sql` declares the initial tables for members, attendance, and payments.
- Add new feature routes under `backend/routes/` and expand database tables in `backend/db/schema.sql`.
