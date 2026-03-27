/**
 * Lightweight local challenge derivation.
 * Uses only existing local player/progress signals.
 */

import type { PlayerData, PuzzleRecord } from "./storage";

export interface ChallengeStatus {
  id: string;
  title: string;
  description: string;
  progressLabel: string;
  done: boolean;
}

function sizeGroupForRecord(record: PuzzleRecord): "small" | "medium" | "large" {
  if (record.gridSize <= 9) return "small";
  if (record.gridSize <= 12) return "medium";
  return "large";
}

export function deriveChallenges(
  data: PlayerData,
  inProgressCount: number
): ChallengeStatus[] {
  const solved = data.puzzlesSolved;
  const noHintRuns = solved.filter((record) => record.hintsUsed === 0).length;
  const noCheckRuns = solved.filter((record) => record.checksUsed === 0).length;
  const solvedGroups = new Set(solved.map(sizeGroupForRecord));
  const largeSolved = solved.filter((record) => record.gridSize >= 13).length;

  return [
    {
      id: "no_hints",
      title: "Fără indicii",
      description: "Rezolvă un rebus fără să folosești ajutoare.",
      progressLabel: `${Math.min(noHintRuns, 1)}/1`,
      done: noHintRuns > 0,
    },
    {
      id: "no_checks",
      title: "Fără verificare",
      description: "Închide un rebus fără butonul Verifică.",
      progressLabel: `${Math.min(noCheckRuns, 1)}/1`,
      done: noCheckRuns > 0,
    },
    {
      id: "resume_progress",
      title: "Reia un rebus în curs",
      description: inProgressCount > 0
        ? "Ai deja un progres salvat. Revino la el din secțiunea Continuă."
        : "Pornește un rebus și lasă-l pregătit pentru o sesiune următoare.",
      progressLabel: inProgressCount > 0 ? `${inProgressCount} gata de reluat` : "0/1",
      done: inProgressCount > 0,
    },
    {
      id: "size_trio",
      title: "Mic + Mediu + Mare",
      description: "Rezolvă câte un rebus din fiecare grupă de mărime.",
      progressLabel: `${solvedGroups.size}/3`,
      done: solvedGroups.size === 3,
    },
    {
      id: "large_finish",
      title: "Termină un rebus mare",
      description: "Închide măcar un rebus 13x13, 14x14 sau 15x15.",
      progressLabel: `${Math.min(largeSolved, 1)}/1`,
      done: largeSolved > 0,
    },
  ];
}

export function pickMenuChallenge(
  challenges: ChallengeStatus[]
): ChallengeStatus | null {
  const resume = challenges.find((challenge) => challenge.id === "resume_progress");
  if (resume && resume.done) {
    return resume;
  }
  return challenges.find((challenge) => !challenge.done) ?? challenges[0] ?? null;
}
