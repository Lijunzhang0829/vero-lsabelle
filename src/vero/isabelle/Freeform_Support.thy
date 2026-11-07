theory Freeform_Support
imports IMPORT_BASE
keywords "contract_reference" :: thy_goal
begin
ML \<open>
structure Freeform_Reference =
struct
val baseline_axioms = map #1 (Thm.all_axioms_of \<^theory>);
val baseline_defs = Theory.defs_of \<^theory>;
val reference_directory = Path.explode "REFERENCE_DIRECTORY";
val long_keyword = Parse_Spec.includes >> K "" || Parse_Spec.long_statement_keyword;
val long_statement =
  Scan.optional (Parse_Spec.opt_thm_name ":" --| Scan.ahead long_keyword) Binding.empty_atts --
  Scan.optional Parse_Spec.includes [] -- Parse_Spec.long_statement
    >> (fn ((binding, includes), (elems, concl)) => (true, binding, includes, elems, concl));
val short_statement = Parse_Spec.statement -- Parse_Spec.if_statement -- Parse.for_fixes
  >> (fn ((shows, assumes), fixes) =>
    (false, Binding.empty_atts, [], [Element.Fixes fixes, Element.Assumes assumes], Element.Shows shows));
val statement = long_statement || short_statement;
fun capture id (long, binding, includes, elems, concl) int lthy =
  let
    val state = Specification.theorem_cmd long Thm.theoremK NONE (K I)
      binding includes elems concl int lthy;
    val {context = ctxt, goal, ...} = Proof.goal state;
    val outer = Proof_Context.init_global (Proof_Context.theory_of lthy);
    val prop = Logic.unprotect (Thm.concl_of goal);
    val premises = Assumption.local_assms_of ctxt outer |> map Thm.term_of;
    val exported = Logic.list_implies (premises, prop)
      |> Assumption.export_term ctxt outer
      |> singleton (Variable.export_terms ctxt outer);
    val path = Path.append reference_directory (Path.basic (id ^ ".yxml"));
    val _ = File.write path (YXML.string_of_body (Term_XML.Encode.term_raw exported));
    val _ = writeln ("CONTRACT_REFERENCE " ^ id);
  in state end;
fun audit id name ctxt =
  let
    val thy = Proof_Context.theory_of ctxt;
    val outer = Proof_Context.init_global thy;
    val expected = File.read (Path.append reference_directory (Path.basic (id ^ ".yxml")))
      |> YXML.parse_body |> Term_XML.Decode.term_raw;
    val candidates = Proof_Context.get_thms ctxt name |> Proof_Context.export ctxt outer;
    (* Join proof futures before reconstructing the goal: a proposition alone
       does not establish that its candidate proof has finished successfully. *)
    val _ = Thm_Deps.all_oracles candidates;
    val ([goal], goal_ctxt) = Variable.import_terms true [expected] outer;
    val result = Goal.prove goal_ctxt [] [] goal
      (fn {context, ...} =>
        REPEAT_DETERM (resolve_tac context [Conjunction.conjunctionI] 1) THEN
        ALLGOALS (resolve_tac context candidates THEN_ALL_NEW
          (Method.assm_tac context ORELSE' resolve_tac context @{thms refl})));
    val _ = if null (Thm.hyps_of result) then () else error "contract: undischarged hypothesis";
    val _ = if Thm_Deps.has_skip_proof [result] then error "contract: skip_proof dependency" else ();
    val _ = if null (Thm_Deps.all_oracles [result]) then () else error "contract: oracle dependency";
    val defs = Defs.dest_constdefs [baseline_defs] (Theory.defs_of thy) |> map #2;
    val allowed = baseline_axioms @ defs;
    val _ = if forall (member (op =) allowed) (map #1 (Thm.all_axioms_of thy))
      then () else error "contract: additional axiom";
    val _ = writeln ("VERO_RESULT " ^ id ^ " prove clean");
  in ctxt end;
end;
val _ = Outer_Syntax.local_theory_to_proof' \<^command_keyword>\<open>contract_reference\<close>
  "capture the frozen proposition without proving or publishing it"
  (Parse.name -- Freeform_Reference.statement
    >> (fn (id, spec) => Freeform_Reference.capture id spec));
\<close>
end
