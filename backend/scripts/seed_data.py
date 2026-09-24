"""The fixture corpus: topics and the documents that back them.

Kept apart from `seed_materials.py` so the runner stays readable and the
content can be edited without touching the write logic.

The prose is deliberately real: goal grounding runs a vector search per
milestone and keeps a hit only under `personalize.GROUNDING_MAX_DISTANCE`
(L2 1.1 ~ cosine 0.45), so lorem ipsum would embed to noise and every
milestone would fall through to the research branch -- which is exactly the
branch this fixture exists to avoid.

Two courses, one semester. Understanding scores span every band on purpose:
`app.ranking` orders milestones by measured weakness, and a corpus where every
topic scored the same would make the ordering untestable.
"""

# name -> (description, user_understanding)
# -1 is "no signal yet", which the ranker treats as neither weak nor strong.
TOPICS: dict[str, tuple[str, int]] = {
    "Big-O Analysis": (
        "Asymptotic notation, cost models and how to compare algorithms.",
        74,
    ),
    "Dynamic Programming": (
        "Overlapping subproblems, memoisation and bottom-up table construction.",
        22,
    ),
    "Graph Algorithms": (
        "Traversal, shortest paths and the greedy proofs behind them.",
        41,
    ),
    "Hash Tables": (
        "Hashing, collision resolution and load factor.",
        58,
    ),
    "Balanced Search Trees": (
        "BSTs, rotations and the invariants that keep height logarithmic.",
        -1,
    ),
    "Eigenvectors and Eigenvalues": (
        "Characteristic polynomials, diagonalisation and what eigenvectors mean.",
        29,
    ),
    "Bayes' Rule": (
        "Conditional probability, priors and posterior updates.",
        66,
    ),
    "Relational Modelling": (
        "Keys, functional dependencies and normal forms.",
        -1,
    ),
    "Concurrency and Locking": (
        "Race conditions, mutual exclusion, deadlock and lock ordering.",
        17,
    ),
}

# Each document is written to disk as `## <topic>` sections. The heading stays
# inside the chunk text: it is the cheapest available topic signal for an
# embedding of a paragraph that never names its own subject.
DOCUMENTS: list[dict] = [
    {
        "filename": "algorithms-lecture-notes.md",
        "upload_type": "text",
        "origin": "local",
        "sections": [
            (
                "Big-O Analysis",
                """Big-O describes how a cost grows as the input grows, and nothing else. It
discards constant factors and lower-order terms, so 3n + 40 and n/2 are both
O(n). That is a feature: the constants depend on the machine, the compiler and
the memory hierarchy, none of which are properties of the algorithm.

Read the notation as an upper bound on growth. O gives an upper bound, Omega a
lower bound, and Theta both at once. Saying insertion sort is O(n^2) is true but
weak; saying it is Theta(n^2) in the worst case and Theta(n) on already-sorted
input is the statement that actually predicts behaviour.

Worst case, average case and amortised cost answer different questions. A
dynamic array push is O(n) in the worst case, because occasionally the whole
array is copied, but O(1) amortised, because the copies are rare enough that
the total cost of m pushes is O(m). Quote amortised cost when the expensive
step pays for itself across a sequence; quote worst case when a single slow
operation would break a latency budget.

The cost model matters as much as the count. We normally charge one unit per
comparison or per array access, which is why a merge sort that is O(n log n) in
comparisons can still lose to an O(n^2) sort on small arrays, where cache
locality dominates the comparison count entirely.""",
            ),
            (
                "Dynamic Programming",
                """Dynamic programming applies when a problem has optimal substructure and
overlapping subproblems. Optimal substructure means an optimal solution is built
from optimal solutions to smaller instances. Overlapping subproblems means the
naive recursion solves the same instance many times, which is what makes
remembering the answers worthwhile.

Start from the recurrence, not the table. For the 0/1 knapsack, best(i, c) is
the best value using the first i items under capacity c, and it is either
best(i-1, c) if the item is skipped or value[i] + best(i-1, c - weight[i]) if it
is taken. Once the recurrence is written, memoisation is a cache keyed on the
arguments, and the bottom-up version is the same recurrence with the arguments
walked in an order that guarantees dependencies are already computed.

The state is the hard part. If the recurrence needs information the state does
not carry, the answers are wrong rather than slow, and the wrongness shows up
only on inputs where the missing information changes the decision. Write down
what the state must know before writing the loop.

Common shapes worth recognising: longest common subsequence over two string
prefixes, edit distance over the same two-index state, coin change over a single
amount, and interval problems where the state is the index of the last accepted
interval. Most exam questions are one of these in costume.

Complexity is states times work per state. Edit distance has n*m states and
constant work per state, so it is O(n*m) time; keeping only the previous row
drops it to O(min(n, m)) space without changing the recurrence at all.""",
            ),
            (
                "Graph Algorithms",
                """Breadth-first search visits vertices in order of edge count from the source and
therefore finds shortest paths in an unweighted graph. Depth-first search does
not, but its visit and finish times are what topological order, cycle detection
and strongly connected components are all built from.

Dijkstra's algorithm generalises BFS to non-negative weights by always expanding
the closest unfinished vertex, which the priority queue supplies. The proof rests
entirely on weights being non-negative: with a negative edge, a vertex can be
finalised before a cheaper path to it is discovered. That is the case
Bellman-Ford handles, at O(V*E), and it is also how Bellman-Ford detects a
negative cycle -- a distance that still improves after V-1 rounds.

Minimum spanning trees come from the cut property: for any cut of the graph, the
lightest edge crossing it belongs to some MST. Kruskal's algorithm applies that
by sorting edges and using union-find to skip the ones that would close a cycle;
Prim's applies it by growing one tree and always taking the lightest edge leaving
it. Both are greedy, and both are correct because of the same property.

Choosing a representation is a real decision. An adjacency list costs O(V + E)
space and makes iterating a vertex's neighbours proportional to its degree; an
adjacency matrix costs O(V^2) but answers "is there an edge" in constant time.
Dense graphs and edge-existence queries favour the matrix, and almost everything
else favours the list.""",
            ),
        ],
    },
    {
        "filename": "ds-recitation-week5.md",
        "upload_type": "text",
        "origin": "local",
        "sections": [
            (
                "Hash Tables",
                """A hash table trades space for time: the hash function turns a key into a bucket
index, so a lookup touches one bucket instead of scanning the table. Average
cost is O(1), worst case is O(n), and the gap between them is entirely about how
well the hash function spreads the keys it is actually given.

Collisions are not an error, they are the normal case. Separate chaining stores
a list per bucket, so a lookup costs one hash plus a short scan, and the table
degrades gracefully as it fills. Open addressing stores entries in the table
itself and probes for the next free slot; it is more cache-friendly, but
deletion needs tombstones, because clearing a slot outright would cut a probe
sequence in half and hide entries behind the hole.

Load factor is the number of entries divided by the number of buckets, and it is
the dial that controls everything. Linear probing degrades sharply past about
0.7 as clusters form; chaining tolerates more. Resizing means allocating a
larger table and rehashing every key, which is O(n) but amortises to O(1) per
insert if the table doubles rather than grows by a constant.

Keys must hash and compare consistently. If a key is mutated after insertion,
its hash changes and the entry becomes unreachable even though it is still in
the table -- the usual reason a lookup "loses" a value that was definitely
stored.""",
            ),
            (
                "Balanced Search Trees",
                """A binary search tree keeps every left descendant smaller than its node and every
right descendant larger, so a search is a sequence of comparisons down one path.
Cost is the height of the tree, which is the problem: inserting sorted keys into
a plain BST produces a path, not a tree, and every operation degrades to O(n).

Balanced trees add an invariant that bounds the height at O(log n) and a repair
step that restores it after each update. An AVL tree keeps the two subtree
heights within one of each other; a red-black tree keeps every root-to-leaf path
within a factor of two by colouring nodes. AVL trees are more rigidly balanced
and therefore faster to search, red-black trees rebalance less and are therefore
faster to update.

Rotations are the repair. A rotation re-parents one child, changes the local
height, and leaves the in-order sequence of keys untouched -- which is precisely
why it is a legal fix: the ordering invariant that makes the tree a search tree
survives it.

In-order traversal of any BST yields the keys in sorted order, which is what a
tree buys over a hash table. A hash table has better constants for lookup, but
it cannot answer "the smallest key above x" or iterate in order, and those are
the queries that decide the data structure in practice.""",
            ),
        ],
    },
    {
        "filename": "linear-algebra-week7.md",
        "upload_type": "text",
        "origin": "drive",
        "drive_file_id": "1SeEdFxTuREdRiVeLiNaLg7",
        "drive_url": "https://docs.google.com/document/d/1SeEdFxTuREdRiVeLiNaLg7/edit",
        "drive_modified_at": "2026-09-12T09:41:00Z",
        "sections": [
            (
                "Eigenvectors and Eigenvalues",
                """An eigenvector of a matrix A is a non-zero vector v whose direction A leaves
alone: A v = lambda v, where the scalar lambda is the eigenvalue and says how
much the vector is stretched. Everything else about eigenvectors follows from
reading that equation as "this direction is only scaled, never turned".

They are found by rewriting the definition as (A - lambda I) v = 0. A non-zero v
exists only if A - lambda I is singular, so det(A - lambda I) = 0. That
determinant is the characteristic polynomial, its roots are the eigenvalues, and
the null space of A - lambda I for each root is that eigenvalue's eigenspace.
For a 2x2 matrix this is a quadratic, which is why every worked example in the
problem set is 2x2 or 3x3.

If an n x n matrix has n linearly independent eigenvectors, it can be
diagonalised: A = P D P^-1, with the eigenvectors as the columns of P and the
eigenvalues along the diagonal of D. The payoff is that A^k = P D^k P^-1, and
raising a diagonal matrix to a power is done entrywise. Repeated eigenvalues do
not always supply enough independent eigenvectors, and a matrix that falls short
is defective and cannot be diagonalised.

Symmetric matrices are the well-behaved case: their eigenvalues are always real
and eigenvectors from distinct eigenvalues are orthogonal, so P can be chosen
orthogonal and P^-1 is just P transposed.

The interpretation is what the exam asks for. Eigenvalues larger than one in
magnitude mean repeated application grows a component, smaller than one means it
decays, and negative means it flips. That is why the largest eigenvalue governs
the long-run behaviour of a Markov chain or a repeated linear update.""",
            ),
        ],
    },
    {
        "filename": "probability-notes.md",
        "upload_type": "text",
        "origin": "drive",
        "drive_file_id": "1SeEdFxTuREdPr0bAbIl1tY",
        "drive_url": "https://docs.google.com/document/d/1SeEdFxTuREdPr0bAbIl1tY/edit",
        "drive_modified_at": "2026-09-16T18:02:00Z",
        "sections": [
            (
                "Bayes' Rule",
                """Conditional probability P(A|B) = P(A and B) / P(B) is the probability of A once B
is known to have happened. Bayes' rule rearranges that into P(A|B) = P(B|A) *
P(A) / P(B), which is what lets an observation update a belief: the prior P(A)
becomes the posterior P(A|B) after seeing the evidence B.

The denominator is usually computed by the law of total probability, P(B) =
P(B|A) P(A) + P(B|not A) P(not A), because the two conditional likelihoods are
what a problem actually gives you.

The classic trap is the base rate. A test that is 99% accurate for a disease
affecting 1 in 10,000 people still produces mostly false positives: of a million
people, 100 are sick and about 99 test positive, while 999,900 are healthy and
about 9,999 test positive anyway. A positive result therefore means roughly a 1%
chance of being sick. The likelihood was strong; the prior was stronger.

Working in natural frequencies rather than percentages makes these questions
much harder to get wrong. Sketch the population, split it by the true state,
then apply the error rates to each branch, and the answer is a ratio of counts.

Independence means P(A and B) = P(A) P(B), equivalently P(A|B) = P(A): learning
B tells you nothing about A. It is not the same as being mutually exclusive --
mutually exclusive events with non-zero probability are maximally dependent,
because one occurring rules the other out entirely.""",
            ),
        ],
    },
    {
        "filename": "db-lab3-normalisation.md",
        "upload_type": "text",
        "origin": "local",
        "sections": [
            (
                "Relational Modelling",
                """A relation is a set of tuples over named attributes, and a key is a set of
attributes whose values identify a tuple uniquely. A candidate key is minimal --
remove any attribute and it stops identifying. The primary key is whichever
candidate key was chosen; a foreign key is an attribute set that must match some
primary key elsewhere, which is how the database enforces that a row cannot
reference something that does not exist.

Functional dependencies drive the whole design. X -> Y means that two tuples
agreeing on X must agree on Y. Normalisation is the process of decomposing
relations until every dependency is a dependency on a key, because a dependency
on something else is a fact stored more than once.

Second normal form removes partial dependencies on part of a composite key;
third normal form removes transitive dependencies, where a non-key attribute
determines another non-key attribute. Boyce-Codd normal form tightens 3NF to
"the left side of every non-trivial dependency is a superkey".

The anomalies are the reason any of this matters. If a student's department is
stored on every enrolment row, then updating it means updating many rows
(update anomaly), a department with no enrolments cannot be recorded at all
(insertion anomaly), and deleting the last enrolment loses the department
(deletion anomaly). Splitting the table so each fact lives in exactly one place
removes all three.

Denormalising is a deliberate trade, not a mistake -- duplicate a column to
avoid a join on a hot read path, and accept that the application now owns the
consistency the schema used to guarantee.""",
            ),
        ],
    },
    {
        "filename": "concurrency-primer.md",
        "upload_type": "text",
        "origin": "local",
        "sections": [
            (
                "Concurrency and Locking",
                """A race condition is any case where the result depends on the interleaving of
threads. The canonical example is two threads incrementing a shared counter:
the increment is a read, an add and a write, and if both threads read before
either writes, one increment disappears. The bug is not in any single line --
it is in the assumption that the three steps happen together.

A critical section is the region that must not be interleaved, and mutual
exclusion is the guarantee that at most one thread is inside it. A mutex
provides that. The cost is serialisation: whatever is inside the lock no longer
runs in parallel, so the lock should cover exactly the shared state and nothing
slow, such as I/O, that does not need protecting.

Deadlock needs four conditions at once: mutual exclusion, hold-and-wait, no
preemption, and a circular wait. Break any one and deadlock is impossible, and
the practical way to break the circular wait is a global lock ordering -- every
thread acquires locks in the same order, so no cycle can form. Two threads that
take locks A then B, and B then A, are the textbook cycle.

Starvation and livelock are the near misses. A starved thread never gets the
lock because others keep winning it; livelocked threads keep reacting to each
other and make no progress while consuming CPU. Neither is deadlock, and neither
is fixed by adding more locks.

Atomics and immutability avoid the problem instead of managing it. An atomic
compare-and-swap performs read-modify-write as one indivisible step, and data
that is never mutated after publication needs no lock at all, because there is
no interleaving that could observe it half-updated.""",
            ),
        ],
    },
]

# A row with no chunks, so the Materials page has a failure state to render and
# the goal graph has a file it must not ground anything on.
FAILED_DOCUMENT = {
    "filename": "scanned-syllabus.pdf",
    "upload_type": "pdf",
    "origin": "local",
    "byte_size": 2_411_008,
    "error": "no usable text found in this file -- the PDF appears to be a scan "
    "without an OCR layer",
}

