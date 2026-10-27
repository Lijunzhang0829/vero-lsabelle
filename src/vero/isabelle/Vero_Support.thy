theory Vero_Support
imports IMPORT_BASE
keywords "vero_prove" "vero_disprove" :: thy_goal
begin

ML \<open>
local
val trusted_axioms = sort_strings (map #1 (Thm.all_axioms_of \<^theory>));
val trusted_defs = Theory.defs_of \<^theory>;

fun safe_def_axioms thy =
  Defs.dest_constdefs [trusted_defs] (Theory.defs_of thy)
  |> filter (fn (name, _) => String.isPrefix "vero_aux_" (Long_Name.base_name name))
  |> map #2;

fun audit id polarity baseline results lthy =
  let
    val thms = flat results;
    val _ = if null thms then error "Vero: no theorem produced" else ();
    val _ = if Thm_Deps.has_skip_proof thms then error "Vero: skip_proof dependency" else ();
    val _ = if null (Thm_Deps.all_oracles thms) then () else error "Vero: oracle dependency";
    val thy = Proof_Context.theory_of lthy;
    val axioms = map #1 (Thm.all_axioms_of thy);
    val allowed = baseline @ safe_def_axioms thy;
    val _ = if forall (member (op =) allowed) axioms then () else error "Vero: new axioms";
    val _ = writeln ("VERO_RESULT " ^ id ^ " " ^ polarity ^ " clean");
  in lthy end;

fun hol_prop (Const ("Pure.imp", _) $ a $ b) = HOLogic.mk_imp (hol_prop a, hol_prop b)
  | hol_prop (Const ("Pure.all", _) $ Abs (x, T, b)) =
      HOLogic.all_const T $ Abs (x, T, hol_prop b)
  | hol_prop (Const ("Pure.conjunction", _) $ a $ b) = HOLogic.mk_conj (hol_prop a, hol_prop b)
  | hol_prop (Const ("Pure.eq", _) $ a $ b) = HOLogic.mk_eq (a, b)
  | hol_prop (Const ("HOL.Trueprop", _) $ p) = p
  | hol_prop _ = error "Vero: unsupported meta-proposition for negation";

val long_keyword = Parse_Spec.includes >> K "" || Parse_Spec.long_statement_keyword;
val long_statement =
  Scan.optional (Parse_Spec.opt_thm_name ":" --| Scan.ahead long_keyword) Binding.empty_atts --
  Scan.optional Parse_Spec.includes [] -- Parse_Spec.long_statement
    >> (fn ((binding, includes), (elems, concl)) => (true, binding, includes, elems, concl));
val short_statement = Parse_Spec.statement -- Parse_Spec.if_statement -- Parse.for_fixes
  >> (fn ((shows, assumes), fixes) =>
    (false, Binding.empty_atts, [], [Element.Fixes fixes, Element.Assumes assumes], Element.Shows shows));

fun positive id (long, binding, includes, elems, concl) int lthy =
  let val baseline = trusted_axioms
  in Specification.theorem_cmd long Thm.theoremK NONE
       (audit id "prove" baseline) binding includes elems concl int lthy end;

fun negative id (_, binding, includes, elems, concl) int lthy =
  let
    val _ = if null includes then () else error "Vero: disprove includes not supported";
    val _ = (case concl of Element.Shows _ => () | _ => error "Vero: disprove obtains not supported");
    val (statements, ctxt) = Expression.read_statement elems concl lthy;
    val goals = maps (map fst o snd) statements;
    val premises = map Thm.term_of (Assumption.local_assms_of ctxt lthy);
    val conclusion = Logic.mk_conjunction_list goals;
    val proposition = Logic.list_implies (premises, conclusion);
    val frees = Term.add_frees proposition []
      |> filter_out (fn (name, _) => Variable.is_fixed lthy name);
    val closed = fold_rev (fn v => fn t => Logic.all (Free v) t) frees proposition;
    val negation = HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop closed));
    val name = if Binding.is_empty (#1 binding)
      then (case statements of (b, _) :: _ => #1 b | _ => Binding.empty)
      else #1 binding;
    val baseline = trusted_axioms;
  in Specification.theorem false Thm.theoremK NONE (audit id "disprove" baseline)
       (name, []) [] [] (Element.Shows [(Binding.empty_atts, [(negation, [])])]) int lthy end;

val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>vero_prove\<close>
  "prove a frozen Vero contract and audit its kernel dependencies"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => positive id spec));
val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>vero_disprove\<close>
  "prove the negation of the universally closed contract, including its own assumptions"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => negative id spec));
in end
\<close>
end
