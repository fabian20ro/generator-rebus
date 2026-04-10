export interface PuzzleSummary {
  id: string;
  title: string;
  theme: string;
  description?: string;
  grid_size: number;
  difficulty: number;
  pass_rate?: number | null;
  created_at: string;
  repaired_at?: string | null;
}

export interface Clue {
  id: string;
  direction: "H" | "V";
  start_row: number;
  start_col: number;
  length: number;
  clue_number: number;
  definition: string;
}

export interface PuzzleDetail {
  puzzle: {
    id: string;
    title: string;
    theme: string;
    description?: string;
    grid_size: number;
    grid_template: string;
    difficulty: number;
    created_at: string;
    repaired_at?: string | null;
  };
  clues: Clue[];
}

export interface PuzzleSolution {
  solution: string;
}
