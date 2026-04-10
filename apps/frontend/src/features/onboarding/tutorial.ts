/**
 * Tutorial / onboarding overlay for first-time users.
 * Shows 4 steps explaining the game, then sets a localStorage flag.
 */

const TUTORIAL_KEY = "rebus_tutorial_seen";

interface TutorialStep {
  title: string;
  description: string;
}

const steps: TutorialStep[] = [
  {
    title: "Bine ai venit la Rebus!",
    description:
      "Aceasta este o aplicatie de rebusuri romanesti interactive. Exploreaza lista de rebusuri disponibile si alege unul pentru a incepe.",
  },
  {
    title: "Selecteaza un rebus",
    description:
      "Fiecare rebus are un cartonas cu titlul si marimea lui. In fila 🧩 poti alege rapid dimensiunea, iar ⏳ si ✅ apar automat dupa ce ai progres sau rezolvari.",
  },
  {
    title: "Completeaza grila",
    description:
      "Apasa pe o celula si tasteaza litere. Foloseste sagetile sau Tab pentru a naviga intre celule. Apasa Enter pentru a schimba directia (orizontal/vertical).",
  },
  {
    title: "Foloseste indiciile",
    description:
      "Butoanele din toolbar iti permit sa verifici raspunsurile, sa dezvalui o litera sau un cuvant. Fiecare indiciu costa puncte, asa ca foloseste-le cu grija!",
  },
];

export function showTutorialIfNeeded(): void {
  try {
    if (localStorage.getItem(TUTORIAL_KEY)) return;
  } catch {
    return; // localStorage unavailable
  }

  let currentStep = 0;

  const overlay = document.createElement("div");
  overlay.className = "tutorial-overlay";

  function render(): void {
    const step = steps[currentStep];
    const isLast = currentStep === steps.length - 1;

    overlay.innerHTML = `
      <div class="tutorial-card">
        <div class="tutorial-step">${currentStep + 1} / ${steps.length}</div>
        <h2 class="tutorial-title">${step.title}</h2>
        <p class="tutorial-description">${step.description}</p>
        <div class="tutorial-actions">
          ${
            isLast
              ? `<button class="btn tutorial-btn" data-action="close">Inchide</button>`
              : `<button class="btn btn-secondary tutorial-btn" data-action="close">Inchide</button>
                 <button class="btn tutorial-btn" data-action="next">Urmatorul</button>`
          }
        </div>
      </div>
    `;
  }

  function closeTutorial(): void {
    overlay.remove();
    try {
      localStorage.setItem(TUTORIAL_KEY, "1");
    } catch {
      // ignore
    }
  }

  overlay.addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest("[data-action]") as HTMLElement | null;
    if (!btn) return;
    const action = btn.dataset.action;
    if (action === "close") {
      closeTutorial();
    } else if (action === "next") {
      currentStep++;
      render();
    }
  });

  render();
  document.body.appendChild(overlay);
}
