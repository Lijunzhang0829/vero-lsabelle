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
  type T = (term * string) Symtab.table;
  val empty = Symtab.empty;
  fun merge tables = Symtab.merge (fn ((a, x), (b, y)) => a aconv b andalso x = y) tables;
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

val hex_digits = "0123456789abcdef";
fun hex_char char =
  let val byte = Char.ord char
  in String.implode [String.sub (hex_digits, byte div 16),
                     String.sub (hex_digits, byte mod 16)] end;
fun hex text = String.translate hex_char text;
fun term_xml term = hex (YXML.string_of_body (Term_XML.Encode.term_raw term));
fun field name value = XML.Elem ((name, []), [XML.Text value]);
fun close_frees predicate term =
  fold_rev (fn v => fn t => Logic.all (Free v) t)
    (filter predicate (Term.add_frees term [])) term;
fun conjunction [] = HOLogic.mk_Trueprop \<^term>\<open>True\<close>
  | conjunction xs = Logic.mk_conjunction_list xs;
fun plain text = XML.content_of (YXML.parse_body text);


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
    val outer = Proof_Context.init_global (Proof_Context.theory_of lthy);
    (* This boundary is authoritative: assumptions added by the lemma are A,
       never owned E, even when they have the same spelling or proposition. *)
    val environment = map Thm.term_of (Assumption.local_assms_of lthy outer);
    val (statements, ctxt) = Expression.read_statement elems concl lthy;
    val assumptions = map Thm.term_of (Assumption.local_assms_of ctxt lthy);
    val goals = maps (map fst o snd) statements;
    val local_terms = [conjunction assumptions,
      Logic.list_implies (assumptions, conjunction goals)]
      |> map (Assumption.export_term ctxt lthy)
      |> Variable.export_terms ctxt lthy;
    val ([assumptions_local, target_local], statement_ctxt) =
      Variable.import_terms true local_terms lthy;
    val statement_parameters = filter (not o Variable.is_fixed lthy o #1)
      (Term.add_frees target_local []);
    val close_statement = close_frees (not o Variable.is_fixed lthy o #1);
    val local_target = close_statement target_local;
    val local_assumptions = close_statement assumptions_local;
    val local_negative = HOLogic.mk_Trueprop (HOLogic.mk_not (hol_prop local_target));
    val exported = [conjunction environment, local_assumptions, local_target,
        Logic.list_implies (environment, local_target),
        Logic.list_implies (environment, local_negative)]
      |> map (Assumption.export_term lthy outer)
      |> Variable.export_terms statement_ctxt outer;
    (* Import the complete tuple together: environment and goal parameters
       cannot silently receive different fresh names or type instantiations. *)
    val ([env, assms, target, positive, negative], global_ctxt) =
      Variable.import_terms true exported outer;
    (* Keep the complete obligation under one HOL judgment. Specification.theorem
       may otherwise turn outer Pure binders into schematic variables before
       its callback, obscuring exact closed-goal comparison. *)
    val closed_positive = HOLogic.mk_Trueprop (hol_prop (close_frees (K true) positive));
    val closed_negative = HOLogic.mk_Trueprop (hol_prop (close_frees (K true) negative));
    val _ = if null (Term.add_frees closed_negative [])
        andalso null (Term.add_vars closed_negative [])
      then () else error "Vero: unclosed owned-context obligation";
    val _ = List.app (fn term => (Thm.cterm_of global_ctxt term; ()))
      [env, assms, target, closed_positive, closed_negative];
    fun term_fields name term = [field (name ^ "_term") (term_xml term),
      field (name ^ "_text") (plain (Syntax.string_of_term global_ctxt term))];
    val scope_fields = [field "result_scope" "owned-context-outcomes-v1",
        field "environment_evidence_status" "not_established_by_negative_certificate",
        field "assumption_boundary" "Expression.read_statement:ctxt-to-owned-lthy",
        field "statement_parameters" (commas (map (fn (name, T) =>
          name ^ " :: " ^ plain (Syntax.string_of_typ statement_ctxt T)) statement_parameters))]
      @ term_fields "environment" env
      @ term_fields "statement_assumptions" assms
      @ term_fields "local_target" target
      @ term_fields "positive_obligation" closed_positive
      @ term_fields "negative_obligation" closed_negative;
    val scope = XML.string_of (XML.Elem (("vero_scope", [("id", id)]), scope_fields));
  in Local_Theory.background_theory
       (Captured_Targets.map (Symtab.update (id, (closed_negative, scope)))) lthy end;

fun negative id name int lthy =
  let
    val (closed, scope) = (case Symtab.lookup (Captured_Targets.get (Proof_Context.theory_of lthy)) id of
        SOME term => term | NONE => error "Vero: refutation target not captured");
    val _ = if null (Assumption.all_assms_of lthy) then ()
      else error "Vero: refutation must run outside owned assumptions";
    (* Freshen type variables against the current context without changing the goal. *)
    val ([closed], lthy) = Variable.import_terms true [Logic.varify_global closed] lthy;
    val negation = closed;
    fun checked results ctxt =
      let
        val thms = flat results;
        val _ = Thm.consolidate thms;
        val thm = (case thms of [thm] => thm | _ => error "Vero: expected one negative theorem");
        val _ = if null (Thm.hyps_of thm) then () else error "Vero: residual hypotheses";
        val actual = Thm.full_prop_of thm;
        val _ = if actual aconv negation orelse actual aconv Logic.varify_global negation
          then () else error "Vero: theorem differs from closed negation";
        val _ = writeln ("VERO_NEGATIVE_SCOPE " ^ id ^ " " ^ hex scope);
      in audit id "disprove" trusted_axioms results ctxt end;
  in Specification.theorem false Thm.theoremK NONE checked
       (name, []) [] [] (Element.Shows [(Binding.empty_atts, [(negation, [])])]) int lthy end;

val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>vero_prove\<close>
  "prove a frozen Vero contract and audit its kernel dependencies"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => positive id spec));
val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>vero_disprove\<close>
  "prove the exact owned-environment-relative negation"
  (Parse.name -- Parse.binding >> (fn (id, name) => negative id name));
val _ = Outer_Syntax.local_theory \<^command_keyword>\<open>vero_refute_capture\<close>
  "export a fixed contextual contract without proving or assuming it"
  (Parse.name -- (long_statement || short_statement) >> (fn (id, spec) => capture id spec));
in end
\<close>
end
