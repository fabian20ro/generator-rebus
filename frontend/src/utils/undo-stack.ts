/**
 * Generic undo/redo stack with configurable max size.
 */
export class UndoStack<T> {
  private undoStack: T[] = [];
  private redoStack: T[] = [];
  private maxSize: number;

  constructor(maxSize = 50) {
    this.maxSize = maxSize;
  }

  push(state: T): void {
    this.undoStack.push(state);
    this.redoStack = [];
    if (this.undoStack.length > this.maxSize) {
      this.undoStack.shift();
    }
  }

  undo(): T | undefined {
    const state = this.undoStack.pop();
    if (state !== undefined) {
      this.redoStack.push(state);
    }
    return state;
  }

  redo(): T | undefined {
    const state = this.redoStack.pop();
    if (state !== undefined) {
      this.undoStack.push(state);
    }
    return state;
  }

  clear(): void {
    this.undoStack = [];
    this.redoStack = [];
  }

  canUndo(): boolean {
    return this.undoStack.length > 0;
  }

  canRedo(): boolean {
    return this.redoStack.length > 0;
  }
}
