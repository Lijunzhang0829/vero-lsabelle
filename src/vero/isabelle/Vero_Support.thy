theory Vero_Support
imports IMPORT_BASE
keywords "vero_prove" "vero_disprove" :: thy_goal
  and "vero_refute_capture" :: thy_decl
begin

ML \<open>
local
val trusted_axioms = sort_strings (map #1 (Thm.all_axioms_of \<^theory>));
val trusted_defs = Theory.defs_of \<^theory>;
structure Captured_Targets = Theory_Data
(
  type T = term Symtab.table;
  val empty = Symtab.empty;
  fun merge tables = Symtab.merge (op aconv) tables;
);

fun safe_def_axioms thy =
  Defs.dest_constdefs [trusted_defs] (Theory.defs_of thy)
  |> filter (fn (name, _) => String.isPrefix "vero_aux_" (Long_Name.base_name name))
  |> map #2;

fun audit id polarity baseline results lthy =
  let
    val thms = flat results;
    val _ = if null thms then error "Vero: no theorem produced" else ();
    val _ = Thm.consolidate thms;
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

fun capture id (_, _, includes, elems, concl) lthy =
  let
    val _ = if null includes then () else error "Vero: disprove includes not supported";
    val _ = (case concl of Element.Shows _ => () | _ => error "Vero: disprove obtains not supported");
    val (statements, ctxt) = Expression.read_statement elems concl lthy;
    val goals = maps (map fst o snd) statements;
    val outer = Proof_Context.init_global (Proof_Context.theory_of ctxt);
    val premises = map Thm.term_of (Assumption.local_assms_of ctxt outer);
    val proposition = Logic.list_implies (premises, Logic.mk_conjunction_list goals);
    val exported = proposition |> Assumption.export_term ctxt outer
      |> singleton (Variable.export_terms ctxt outer);
    val ([imported], global_ctxt) = Variable.import_terms true [exported] outer;
    val closed = fold_rev (fn v => fn t => Logic.all (Free v) t)
      (Term.add_frees imported []) imported;
    val _ = if null (Term.add_frees closed []) andalso null (Term.add_vars closed [])
      then () else error "Vero: unclosed contextual target";
    val _ = Thm.cterm_of global_ctxt closed;
    val _ = Thm.cterm_of global_ctxt (HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop closed)));
  in Local_Theory.background_theory
       (Captured_Targets.map (Symtab.update (id, closed))) lthy end;

fun negative id name int lthy =
  let
    val closed = (case Symtab.lookup (Captured_Targets.get (Proof_Context.theory_of lthy)) id of
        SOME term => term | NONE => error "Vero: refutation target not captured");
    val _ = if null (Assumption.all_assms_of lthy) then ()
      else error "Vero: refutation must run outside owned assumptions";
    val negation = HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop closed));
    fun checked results ctxt =
      let
        val thms = flat results;
        val _ = Thm.consolidate thms;
        val thm = (case thms of [thm] => thm | _ => error "Vero: expected one negative theorem");
        val _ = if null (Thm.hyps_of thm) then () else error "Vero: residual hypotheses";
        val actual = Thm.full_prop_of thm;
        val _ = if actual aconv negation orelse actual aconv Logic.varify_global negation
          then () else error "Vero: theorem differs from closed negation";
      in audit id "disprove" trusted_axioms results ctxt end;
  in Specification.theorem false Thm.theoremK NONE checked
       (name, []) [] [] (Element.Shows [(Binding.empty_atts, [(negation, [])])]) int lthy end;

val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>vero_prove\<close>
  "prove a frozen Vero contract and audit its kernel dependencies"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => positive id spec));
val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>vero_disprove\<close>
  "prove the closed negation outside the captured contract context"
  (Parse.name -- Parse.binding >> (fn (id, name) => negative id name));
val _ = Outer_Syntax.local_theory \<^command_keyword>\<open>vero_refute_capture\<close>
  "export a fixed contextual contract without proving or assuming it"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => capture id spec));
in end
\<close>
end
