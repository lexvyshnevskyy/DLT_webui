"""CSS for programs list built from Gradio Rows (no HTML table / JS)."""

PROGRAMS_TABLE_CSS = """
.del-prog-wrap {
  width: 100%;
  margin: 0.75rem 0 1.25rem;
  border: 1px solid var(--border-color-primary);
  border-radius: var(--radius-lg, 12px);
  overflow: hidden;
  background: var(--background-fill-primary);
  box-shadow: var(--shadow-drop, 0 1px 2px rgba(0,0,0,.06));
}
.del-prog-head,
.del-prog-row {
  display: grid !important;
  grid-template-columns: minmax(3.5rem, 0.5fr) minmax(10rem, 2fr) minmax(5rem, 0.9fr) repeat(3, minmax(6.5rem, 1fr));
  gap: 0.5rem 0.75rem !important;
  align-items: center !important;
  width: 100% !important;
  margin: 0 !important;
  padding: 0.55rem 1rem !important;
  border: none !important;
  box-shadow: none !important;
}
.del-prog-head {
  background: var(--table-even-background-fill, var(--background-fill-secondary));
  border-bottom: 1px solid var(--border-color-primary) !important;
  font-weight: 600;
  font-size: var(--text-md, 14px);
  color: var(--body-text-color-subdued, var(--body-text-color));
}
.del-prog-head .del-prog-head-label p { margin: 0; font-weight: 600; }
.del-prog-row {
  border-bottom: 1px solid var(--border-color-primary) !important;
  background: var(--background-fill-primary);
}
.del-prog-row:last-child { border-bottom: none !important; }
.del-prog-row:nth-child(even) {
  background: var(--table-even-background-fill, var(--background-fill-secondary));
}
.del-prog-cell { min-width: 0 !important; }
.del-prog-cell > .wrap,
.del-prog-cell > .wrap > button {
  width: 100% !important;
  min-height: unset !important;
  height: auto !important;
  padding: 0.15rem 0 !important;
  justify-content: flex-start !important;
  text-align: left !important;
  font-weight: 500 !important;
  border: none !important;
  box-shadow: none !important;
  background: transparent !important;
  color: var(--link-text-color, var(--color-accent)) !important;
  text-decoration: underline;
}
.del-prog-cell > .wrap > button:hover {
  opacity: 0.85;
  background: transparent !important;
}
.del-prog-action > .wrap > button {
  width: 100% !important;
  font-size: var(--text-sm, 13px) !important;
}
.del-prog-delete > .wrap > button {
  color: var(--error-text-color, #b91c1c) !important;
  border-color: var(--error-border-color, #fca5a5) !important;
}
.del-prog-empty {
  padding: 1.25rem 1rem;
  color: var(--body-text-color-subdued);
  font-style: italic;
  text-align: center;
}
"""
