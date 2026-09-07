// Single source of truth for TicketX QR payload format.
// Generation (TicketPreview) encodes this exact string into the QR.
// Scanning (ScannerPanel) decodes + normalizes back to this exact string.
// Canonical form: TX-<ticket_id>.<hex_hmac>  e.g. TX-12.ab12...

const TX_PATTERN = /TX-\d+\.[0-9a-f]{64}/i;

export function normalizeTxPayload(value) {
  return (value ?? "").trim();
}

export function isTxPayload(value) {
  return TX_PATTERN.test(normalizeTxPayload(value)) && normalizeTxPayload(value).match(new RegExp(`^${TX_PATTERN.source}$`, "i")) !== null;
}

// Camera decoders sometimes return whitespace, newlines, URLs, or JSON
// wrapping the TX. Extract the canonical TX so generation and scan agree.
export function extractTxPayload(decodedText) {
  const text = normalizeTxPayload(decodedText);
  if (!text) return "";
  const match = text.match(TX_PATTERN);
  if (match) return match[0];
  return text;
}
