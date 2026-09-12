Lean-to-Isabelle correspondence:
  Multiset \<nat>                  ~  nat multiset
  m ::ₘ s                     ~  add_mset m s
  Multiset.card               ~  size
  a \<in> B                       ~  a \<in># B
  B.filter P                  ~  filter_mset P B
  Relation.ReflTransGen Move  ~  rtranclp Move (notation Move)
  padicValNat p a             ~  multiplicity p a  (both agree with the
                                 p-adic valuation whenever p is prime, and
                                 gExp is only ever applied to primes in this
                                 development; for non-prime p they need not
                                 be 0, but that discrepancy is inert here.
                                 Even the impossible case a = 0 agrees:
                                 multiplicity p 0 = 0 = padicValNat p 0)
  Multiset.gcd (fold gcd 0)   ~  Gcd on the *set* of valuations.  Since
                                 gcd(a, 0) = a and gcd is idempotent,
                                 duplicates and zero entries do not matter,
                                 so Gcd over set_mset computes the same value;
                                 Gcd {} = 0 matches Multiset.gcd \<emptyset> = 0.
  B.prod                      ~  prod_mset B
  Nat.primeFactors            ~  prime_factors (both are {} for argument 0)
  Lean's `set_option backward.isDefEq.respectTransparency false` has no
  Isabelle analogue and is dropped.