const PENCIL_HELP_KEY = "rebus_pencil_help_seen";
let pencilHelpSeenInMemory = false;

function hasSeenPencilHelp(): boolean {
  if (pencilHelpSeenInMemory) {
    return true;
  }

  try {
    return localStorage.getItem(PENCIL_HELP_KEY) === "1";
  } catch {
    return false;
  }
}

function markPencilHelpSeen(): void {
  pencilHelpSeenInMemory = true;

  try {
    localStorage.setItem(PENCIL_HELP_KEY, "1");
  } catch {
    // ignore storage failures
  }
}

export async function showPencilHelpIfNeeded(): Promise<boolean> {
  if (hasSeenPencilHelp()) {
    return true;
  }

  const shouldEnable = await showPencilHelpModal();
  markPencilHelpSeen();
  return shouldEnable;
}

function showPencilHelpModal(): Promise<boolean> {
  return new Promise((resolve) => {
    const overlay = document.createElement("div");
    overlay.className = "modal";

    overlay.innerHTML = `
      <div class="modal-content pencil-help" role="dialog" aria-modal="true" aria-labelledby="pencil-help-title">
        <h2 id="pencil-help-title" class="pencil-help__title">Mod creion</h2>
        <p class="pencil-help__description">
          Literele introduse în mod creion sunt marcate ca tentative.
        </p>
        <ul class="pencil-help__list">
          <li>rămân vizibil diferite față de răspunsurile finale</li>
          <li>verificarea și indiciile funcționează la fel</li>
          <li>util când vrei să încerci variante fără să te încurci</li>
        </ul>
        <div class="tutorial-actions pencil-help__actions">
          <button class="btn btn-secondary tutorial-btn" type="button" data-action="dismiss">Nu acum</button>
          <button class="btn tutorial-btn" type="button" data-action="enable">Activează creionul</button>
        </div>
      </div>
    `;

    function close(result: boolean): void {
      overlay.remove();
      resolve(result);
    }

    overlay.addEventListener("click", (e) => {
      const target = e.target as HTMLElement;
      if (target === overlay) {
        close(false);
        return;
      }

      const btn = target.closest("[data-action]") as HTMLElement | null;
      if (!btn) return;

      close(btn.dataset.action === "enable");
    });

    document.body.appendChild(overlay);
  });
}
