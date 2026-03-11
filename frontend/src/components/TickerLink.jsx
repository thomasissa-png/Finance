import React from "react";
import { tickerName, tickerTdUrl } from "../utils/format";

/**
 * Clickable ticker that opens the Twelve Data (or Yahoo Finance for indices) chart page.
 * Props:
 *   ticker   — yfinance-format ticker string (e.g. "ZW=F", "BNP.PA")
 *   raw      — if true, display raw ticker instead of readable name
 *   className — optional CSS class
 *   style    — optional inline style
 */
export default function TickerLink({ ticker, raw, className, style }) {
  if (!ticker) return <span className={className} style={style}>--</span>;
  const url = tickerTdUrl(ticker);
  const label = raw ? ticker : tickerName(ticker);
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={className}
      style={{ color: "inherit", textDecoration: "none", borderBottom: "1px dashed var(--text-muted)", ...style }}
      title={`Voir ${ticker} sur ${url?.includes("yahoo") ? "Yahoo Finance" : "Twelve Data"}`}
      onClick={(e) => e.stopPropagation()}
    >
      {label}
    </a>
  );
}
