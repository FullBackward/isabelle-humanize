theory putnam_2023_b5 imports Complex_Main
"HOL-Number_Theory.Cong"
"HOL-Combinatorics.Permutations"
begin

(* Answer inlined (benchmark mode): {n : n = 1 or n = 2 (mod 4)}, matching
   the official solution (kskedlaya.org/putnam-archive/2023s.pdf, B5) and
   the upstream PutnamBench comment. *)
definition putnam_2023_b5_solution :: "int set" where
  "putnam_2023_b5_solution \<equiv> {n::int. n = 1 \<or> [n = 2] (mod 4)}"

theorem putnam_2023_b5:
  fixes n :: int
  and perm :: bool
  assumes hperm: "perm = (\<forall>m::int. coprime m n \<longrightarrow> (\<exists>p::int\<Rightarrow>int. p permutes {1..n} \<and> (\<forall>k::int\<in>{1..n}. [p (p k) = m*k] (mod n))))"
  shows "(n > 0 \<and> perm) \<longleftrightarrow> n \<in> putnam_2023_b5_solution"
  sorry

end
