theory putnam_2023_b6 imports Complex_Main
"HOL-Analysis.Determinants"
begin

(* Corrected encoding (2026-09-04): pairs bounded to a,b >= 0, matching the
   official solution (S11 = n+1 "corresponding to (0,n),...,(n,0)").
   The upstream PutnamBench Isabelle file counts over unrestricted int,
   which makes every entry's set infinite (card = 0) and det S = 0 --
   internally inconsistent with the official answer.
   Answer inlined (benchmark mode): (-1)^(ceil(n/2)-1) * 2*ceil(n/2),
   verified against kskedlaya.org/putnam-archive/2023s.pdf for n = 1, 2. *)

(* uses (nat \<Rightarrow> 'n) instead of (Fin n \<Rightarrow> 'n) *)
definition putnam_2023_b6_solution :: "int \<Rightarrow> int" where
  "putnam_2023_b6_solution \<equiv> (\<lambda>n::int. (-1)^(nat \<lceil>(rat_of_int n)/2\<rceil> - 1) * 2 * \<lceil>(rat_of_int n)/2\<rceil>)"

theorem putnam_2023_b6:
  fixes n :: int
  and S :: "int^'n^'n"
  assumes npos: "n > 0"
  and pncard: "CARD('n) = (nat n)"
  and hS: "\<exists>pnind::int\<Rightarrow>'n. (pnind ` {0..(n-1)} = UNIV \<and> (\<forall>i::int\<in>{0..(n-1)}. \<forall>j::int\<in>{0..(n-1)}. S$(pnind i)$(pnind j) = card {(a::int,b::int). a \<ge> 0 \<and> b \<ge> 0 \<and> a*(i+1) + b*(j+1) = n}))"
  shows "det S = putnam_2023_b6_solution n"
  sorry

end
