theory imo_2026_p1
  imports "HOL-Computational_Algebra.Computational_Algebra"
          "HOL-Library.Multiset"
begin

section \<open>IMO 2026, Problem 1 (Confucius' gcd/lcm blackboard game)\<close>

text \<open>Informal statement (IMO 2026, Shanghai):

  There are 2026 integers greater than 1 written on a blackboard, not
  necessarily different.  In a move, Confucius chooses two integers m > 1 and
  n > 1 from *different places* on the blackboard and replaces these two
  integers with

        gcd(m, n)     and     lcm(m, n) / gcd(m, n).

  He continues to make moves while it is possible to do so.

  (a) Prove that, regardless of the choices of Confucius, after finitely many
      moves, exactly one integer M on the blackboard is greater than 1.
  (b) Prove that the value of M does not depend on the choices of Confucius.
\<close>

subsection \<open>Boards and the move relation\<close>

text \<open>A \<^emph>\<open>board\<close> is a finite multiset of natural numbers.  The full board
discipline (entries \<open>\<ge> 1\<close>, cardinality \<open>2026\<close>) is captured by the predicate
\<const>\<open>IsInitial\<close>.\<close>

type_synonym board = "nat multiset"

text \<open>An \<^emph>\<open>initial board\<close>: exactly \<open>2026\<close> entries, each strictly greater
than \<open>1\<close>.\<close>

definition IsInitial :: "board \<Rightarrow> bool" where
  "IsInitial B \<equiv> size B = 2026 \<and> (\<forall>a \<in># B. 1 < a)"

text \<open>A single \<^emph>\<open>move\<close>: pick two entries \<open>m, n\<close> (from two distinct
positions, modelled as two separate elements of the multiset) both \<open>> 1\<close>,
remove them and insert \<open>gcd m n\<close> and \<open>lcm m n div gcd m n\<close>.  Using
\<term>\<open>add_mset m (add_mset n s)\<close> for the source board automatically encodes
that the two chosen positions are distinct (they are two separate multiset
elements, whose \<^emph>\<open>values\<close> may coincide).\<close>

definition Move :: "board \<Rightarrow> board \<Rightarrow> bool" where
  "Move B B' \<equiv> \<exists>m n s. 1 < m \<and> 1 < n \<and>
     B = add_mset m (add_mset n s) \<and>
     B' = add_mset (gcd m n) (add_mset (lcm m n div gcd m n) s)"

text \<open>A board is \<^emph>\<open>terminal\<close> when at most one entry is \<open>> 1\<close>, so no move is
possible.\<close>

definition IsTerminal :: "board \<Rightarrow> bool" where
  "IsTerminal B \<equiv> size (filter_mset (\<lambda>a. 1 < a) B) \<le> 1"

text \<open>A board has a \<^emph>\<open>unique large entry\<close> when exactly one entry is \<open>> 1\<close>.\<close>

definition HasUniqueLarge :: "board \<Rightarrow> bool" where
  "HasUniqueLarge B \<equiv> size (filter_mset (\<lambda>a. 1 < a) B) = 1"

text \<open>\<open>Reachable B B'\<close>: \<open>B'\<close> can be obtained from \<open>B\<close> by a finite sequence
of moves (the reflexive--transitive closure of \<open>Move\<close>).  A finite play from
\<open>B\<close> to a terminal board \<open>B'\<close> is precisely a witness of \<open>Reachable B B'\<close>
with \<open>IsTerminal B'\<close>.\<close>

definition Reachable :: "board \<Rightarrow> board \<Rightarrow> bool" where
  "Reachable \<equiv> rtranclp Move"

subsection \<open>The invariant value\<close>

text \<open>The exponent \<open>g_p\<close> for a prime \<open>p\<close> and board \<open>B\<close>: the \<open>gcd\<close> of the
\<open>p\<close>-adic valuations of the entries of \<open>B\<close>.  Since \<open>gcd(a, 0) = a\<close>, valuations
equal to \<open>0\<close> (entries not divisible by \<open>p\<close>) do not affect this gcd, so
\<open>gExp p B\<close> is the gcd of the \<^emph>\<open>positive\<close> \<open>p\<close>-adic valuations occurring in
\<open>B\<close>.\<close>

definition gExp :: "nat \<Rightarrow> board \<Rightarrow> nat" where
  "gExp p B \<equiv> Gcd (set_mset (image_mset (multiplicity p) B))"

text \<open>The claimed invariant terminal value
\<open>M = \<Prod> p \<in> prime_factors (prod_mset B). p ^ gExp p B\<close>, the product over all
primes dividing some entry of \<open>B\<close> of \<open>p\<close> raised to the gcd of the \<open>p\<close>-adic
valuations.\<close>

definition Mval :: "board \<Rightarrow> nat" where
  "Mval B \<equiv> \<Prod>p \<in> prime_factors (prod_mset B). p ^ gExp p B"

subsection \<open>The theorems (statement (a), part 1: termination)\<close>

text \<open>\<^bold>\<open>Statement (a), part 1 --- termination.\<close>  There is no infinite play
starting from an initial board \<open>B0\<close>: no infinite sequence of boards can start
at \<open>B0\<close> and have every consecutive pair related by a \<open>Move\<close>.\<close>

theorem statement_a_termination:
  assumes "IsInitial B0"
  shows "\<nexists>f :: nat \<Rightarrow> board. f 0 = B0 \<and> (\<forall>k. Move (f k) (f (k + 1)))"
  sorry

text \<open>\<^bold>\<open>Statement (a), part 2 --- unique large entry.\<close>  Any terminal board
reachable from an initial board \<open>B0\<close> has exactly one entry \<open>> 1\<close>.\<close>

theorem statement_a_unique_large:
  assumes "IsInitial B0" and "Reachable B0 B'" and "IsTerminal B'"
  shows "HasUniqueLarge B'"
  sorry

subsection \<open>Statement (b): invariance of the final value\<close>

text \<open>\<^bold>\<open>Statement (b) --- invariance of \<open>M\<close>.\<close>  Any two terminal boards
reachable from the same initial board \<open>B0\<close> have the same set of entries
\<open>> 1\<close>; since (by (a)) each has exactly one such entry, this says the terminal
value \<open>M\<close> is the same for both.\<close>

theorem statement_b_invariance:
  assumes "IsInitial B0"
    and "Reachable B0 B1" and "Reachable B0 B2"
    and "IsTerminal B1" and "IsTerminal B2"
  shows "\<forall>M. (1 < M \<and> M \<in># B1) \<longleftrightarrow> (1 < M \<and> M \<in># B2)"
  sorry

text \<open>\<^bold>\<open>Value of \<open>M\<close> (correctness of the explicit formula).\<close>  For any
terminal board \<open>B'\<close> reachable from an initial board \<open>B0\<close>, the unique entry
\<open>M > 1\<close> of \<open>B'\<close> equals the invariant \<open>Mval B0\<close>.\<close>

theorem terminal_value_eq_Mval:
  assumes "IsInitial B0" and "Reachable B0 B'" and "IsTerminal B'"
    and "1 < M" and "M \<in># B'"
  shows "M = Mval B0"
  sorry

text \<open>The invariant terminal value is itself \<open>> 1\<close>, since all initial entries
exceed \<open>1\<close>.\<close>

theorem Mval_gt_one:
  assumes "IsInitial B0"
  shows "1 < Mval B0"
  sorry

end
