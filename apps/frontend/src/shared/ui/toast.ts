/**
 * Simple toast notification system.
 */

export function showToast(
  message: string,
  variant: "info" | "warning" = "info",
  duration = 3000
): void {
  const container = document.getElementById("toast-container");
  if (!container) return;

  const toast = document.createElement("div");
  toast.className = `toast ${variant === "warning" ? "toast--warning" : ""}`;
  toast.textContent = message;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.3s";
    setTimeout(() => toast.remove(), 300);
  }, duration);
}
