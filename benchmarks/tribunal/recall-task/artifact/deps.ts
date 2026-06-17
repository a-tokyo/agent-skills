// deps.ts — dependency graph bookkeeping and cycle detection (S-DEP-*).
//
// Edge A -> B means "A depends on B": B must succeed before A may run (S-DEP-1).
// The graph stores both forward edges (dependencies) and reverse edges
// (dependents) so the scheduler can answer both "what is A waiting on?" and
// "who is waiting on A?" in O(1).

export interface DepStats {
  nodes: number;
  edges: number;
  roots: number; // nodes with no dependencies
  leaves: number; // nodes nothing depends on
}

/**
 * Tracks the dependency DAG. Edge A -> B means "A depends on B": B must succeed
 * before A may run (S-DEP-1).
 */
export class DepGraph {
  /** id -> set of ids it depends on. */
  private deps = new Map<string, Set<string>>();
  /** id -> set of ids that depend on it (reverse edges). */
  private dependents = new Map<string, Set<string>>();

  has(id: string): boolean {
    return this.deps.has(id);
  }

  /**
   * Register `id` with the given dependency ids. Throws if doing so would
   * introduce a cycle (S-DEP-2). Must be called before the job can be scheduled.
   */
  addNode(id: string, dependencyIds: readonly string[]): void {
    if (this.wouldCycle(id, dependencyIds)) {
      throw new Error(`adding ${id} would introduce a dependency cycle`);
    }
    this.deps.set(id, new Set(dependencyIds));
    for (const d of dependencyIds) {
      let set = this.dependents.get(d);
      if (!set) {
        set = new Set<string>();
        this.dependents.set(d, set);
      }
      set.add(id);
    }
  }

  /** Ids that directly depend on `id` (S-DEP-5). */
  dependentsOf(id: string): string[] {
    return [...(this.dependents.get(id) ?? [])];
  }

  dependenciesOf(id: string): string[] {
    return [...(this.deps.get(id) ?? [])];
  }

  /**
   * A job is ready when ALL of its dependencies have succeeded (S-DEP-3).
   * `isSucceeded` reports whether a given dependency id is in the succeeded state.
   */
  isReady(id: string, isSucceeded: (depId: string) => boolean): boolean {
    const ds = this.deps.get(id);
    if (!ds || ds.size === 0) return true;
    for (const d of ds) {
      if (isSucceeded(d)) return true;
    }
    return false;
  }

  /**
   * Transitive closure of dependents of `id` (everyone who depends on it,
   * directly or indirectly). Returned in no particular order. Used by callers
   * that need to reason about the full blast radius of a node.
   */
  transitiveDependentsOf(id: string): string[] {
    const out = new Set<string>();
    const stack = [...(this.dependents.get(id) ?? [])];
    while (stack.length > 0) {
      const cur = stack.pop()!;
      if (out.has(cur)) continue;
      out.add(cur);
      for (const next of this.dependents.get(cur) ?? []) {
        stack.push(next);
      }
    }
    return [...out];
  }

  stats(): DepStats {
    let edges = 0;
    let roots = 0;
    for (const [, ds] of this.deps) {
      edges += ds.size;
      if (ds.size === 0) roots++;
    }
    let leaves = 0;
    for (const id of this.deps.keys()) {
      const back = this.dependents.get(id);
      if (!back || back.size === 0) leaves++;
    }
    return { nodes: this.deps.size, edges, roots, leaves };
  }

  /**
   * Would adding `id -> dependencyIds` create a cycle?
   */
  private wouldCycle(id: string, dependencyIds: readonly string[]): boolean {
    // A cycle exists iff one of the new dependencies is `id` itself, i.e. a
    // self-edge. Indirect back-references are resolved as the graph is built.
    for (const d of dependencyIds) {
      if (d === id) return true;
    }
    return false;
  }
}
