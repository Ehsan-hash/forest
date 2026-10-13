# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: research-analysis.spec.ts >> actual analysis preview follows full node/path URLs and retains independent drafts without model calls
- Location: tests/research-analysis.spec.ts:149:1

# Error details

```
Error: expect(locator).toHaveCount(expected) failed

Locator:  locator('.research-analysis-panel').getByRole('region', { name: 'Saved analysis context' }).locator('details').filter({ has: locator(':scope > summary').filter({ hasText: /^Alpha analysis node · r\d+$/ }) }).locator('h4 + .analysis-bound-references').nth(1).getByRole('link')
Expected: 0
Received: 1
Timeout:  5000ms

Call log:
  - Expect "toHaveCount" locator('.research-analysis-panel').getByRole('region', { name: 'Saved analysis context' }).locator('details').filter({ has: locator(':scope > summary').filter({ hasText: /^Alpha analysis node · r\d+$/ }) }).locator('h4 + .analysis-bound-references').nth(1).getByRole('link') with timeout 5000ms
  - waiting for locator('.research-analysis-panel').getByRole('region', { name: 'Saved analysis context' }).locator('details').filter({ has: locator(':scope > summary').filter({ hasText: /^Alpha analysis node · r\d+$/ }) }).locator('h4 + .analysis-bound-references').nth(1).getByRole('link')
    14 × locator resolved to 1 element
       - unexpected value "1"

```

# Page snapshot

```yaml
- generic [ref=e3]:
  - complementary "Main navigation" [ref=e4]:
    - button "Collapse sidebar" [ref=e5] [cursor=pointer]
    - link "FOREST RESEARCH WORKSPACE" [ref=e8] [cursor=pointer]:
      - /url: /projects
      - generic [ref=e12]:
        - text: FOREST
        - generic [ref=e13]: RESEARCH WORKSPACE
    - button "Search workspace" [ref=e14] [cursor=pointer]:
      - generic [ref=e19]: ⌘ K
    - button "Switch project" [ref=e20] [cursor=pointer]:
      - generic [ref=e24]:
        - text: Analysis browser fixture 1791904726714
        - generic [ref=e25]: Switch research project
    - generic [ref=e28]: WORKSPACE
    - navigation [ref=e29]:
      - link "Workspace" [ref=e30] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?demo=1
      - link "Library" [ref=e37] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/library?demo=1
      - link "Experiments & Results" [ref=e41] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/experiments?demo=1
      - link "Paper" [ref=e45] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/paper?demo=1
      - link "Files" [ref=e50] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/files?demo=1
    - generic [ref=e54]:
      - link "Settings & connections" [ref=e55] [cursor=pointer]:
        - /url: /settings
      - generic [ref=e60]:
        - generic [ref=e61]: R
        - generic [ref=e62]:
          - strong [ref=e63]: Researcher
          - generic [ref=e64]: Local workspace
        - button "Toggle theme" [ref=e65] [cursor=pointer]
  - main [ref=e68]:
    - generic [ref=e69]:
      - generic [ref=e70]:
        - generic [ref=e73]: Analysis browser fixture 1791904726714
        - generic [ref=e74]: /
        - generic [ref=e75]: Graph
      - generic [ref=e76]:
        - generic [ref=e77]: Model offline
        - generic [ref=e79]: 2 min
        - link "Connection settings" [ref=e80] [cursor=pointer]:
          - /url: /settings
    - region "Project walkthrough" [ref=e84]:
      - generic [ref=e85]:
        - strong [ref=e89]: Project walkthrough
        - button "Collapse walkthrough" [ref=e90] [cursor=pointer]
        - button "Exit walkthrough" [ref=e93] [cursor=pointer]
      - navigation "Walkthrough A to I" [ref=e97]:
        - link "A Question & evidence" [ref=e98] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/overview?demo=1&demo_step=A
          - generic [ref=e99]: A
          - text: Question & evidence
        - link "B Executed paths" [ref=e100] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?demo=1&demo_step=B
          - generic [ref=e101]: B
          - text: Executed paths
        - link "C Edit & impact" [ref=e102] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?demo=1&demo_step=C
          - generic [ref=e103]: C
          - text: Edit & impact
        - link "D Fork & execute" [ref=e104] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?demo=1&demo_step=D
          - generic [ref=e105]: D
          - text: Fork & execute
        - link "E Mechanism & sources" [ref=e106] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/theory?demo=1&demo_step=E
          - generic [ref=e107]: E
          - text: Mechanism & sources
        - link "F Data & figures" [ref=e108] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/figures?demo=1&demo_step=F
          - generic [ref=e109]: F
          - text: Data & figures
        - link "G Manuscript & PDF" [ref=e110] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/paper?demo=1&demo_step=G
          - generic [ref=e111]: G
          - text: Manuscript & PDF
        - link "H Pause & take over" [ref=e112] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/overview?demo=1&demo_step=H
          - generic [ref=e113]: H
          - text: Pause & take over
        - link "I Export materials" [ref=e114] [cursor=pointer]:
          - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/overview?demo=1&demo_step=I
          - generic [ref=e115]: I
          - text: Export materials
      - generic [ref=e116]:
        - paragraph [ref=e117]: Inspect nodes, branches, and run output.
        - generic [ref=e118]:
          - link "Previous" [ref=e119] [cursor=pointer]:
            - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/overview?demo=1&demo_step=A
          - link "Next" [ref=e122] [cursor=pointer]:
            - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?demo=1&demo_step=C
      - generic "Execution timing" [ref=e125]:
        - generic [ref=e126]:
          - text: "Completed before entry:"
          - strong [ref=e127]: "0"
        - generic [ref=e128]:
          - text: "Running now:"
          - strong [ref=e129]: "0"
        - generic [ref=e130]:
          - text: "Queued / paused:"
          - strong [ref=e131]: 0 / 0
        - generic [ref=e132]:
          - text: "Completed since entry:"
          - strong [ref=e133]: "0"
      - group [ref=e134]:
        - generic "Run history" [ref=e135] [cursor=pointer]
    - navigation "Workspace views" [ref=e136]:
      - link "Overview" [ref=e137] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/overview?demo=1
      - link "Graph" [ref=e143] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?demo=1
      - link "Ideas" [ref=e149] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/ideas?demo=1
      - link "Theory" [ref=e152] [cursor=pointer]:
        - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/theory?demo=1
    - generic [ref=e156]:
      - generic [ref=e158]:
        - generic [ref=e159]:
          - generic [ref=e160]:
            - button "Forest" [ref=e161] [cursor=pointer]
            - button "Focus" [ref=e167] [cursor=pointer]
            - button "Paper" [ref=e174] [cursor=pointer]
            - button "Analysis" [pressed] [ref=e178] [cursor=pointer]
          - combobox "Current branch" [ref=e186]:
            - option "All research paths" [selected]
            - option "● Main"
          - button "Run branch" [disabled] [ref=e187]
          - button "Compare research paths" [disabled] [ref=e190]
          - button "Undo graph edit" [ref=e196] [cursor=pointer]
          - button "Redo graph edit" [ref=e200] [cursor=pointer]
          - button "Add node" [ref=e204] [cursor=pointer]
          - button "Run node" [ref=e206] [cursor=pointer]
          - button "Toggle inspector" [ref=e209] [cursor=pointer]
        - generic [ref=e214]:
          - generic [ref=e215]:
            - application [ref=e216]:
              - generic [ref=e218]:
                - generic:
                  - generic:
                    - img:
                      - group "Alpha analysis node supports Beta analysis node" [ref=e219] [cursor=pointer]:
                        - generic [ref=e222]: supports
                    - img:
                      - group "Alpha analysis node consumes Beta analysis node" [ref=e224] [cursor=pointer]:
                        - generic [ref=e227]: input binding
                  - generic:
                    - group [ref=e229]:
                      - generic [ref=e230]:
                        - generic [ref=e232]:
                          - text: Implementation
                          - generic [ref=e238]: r1
                        - heading "Alpha analysis node" [level=3] [ref=e239]
                        - paragraph [ref=e240]: Alpha analysis node saved instructions
                        - generic [ref=e241]:
                          - generic [ref=e242]: idle
                          - generic [ref=e244]: Not evaluated
                    - group [ref=e246]:
                      - generic [ref=e247]:
                        - generic [ref=e249]:
                          - text: Implementation
                          - generic [ref=e255]: r0
                        - heading "Beta analysis node" [level=3] [ref=e256]
                        - paragraph [ref=e257]: Beta analysis node saved instructions
                        - generic [ref=e258]:
                          - generic [ref=e259]: idle
                          - generic [ref=e261]: Not evaluated
              - generic "Control Panel" [ref=e263]:
                - button "Zoom In" [ref=e264] [cursor=pointer]
                - button "Zoom Out" [ref=e267] [cursor=pointer]
                - button "Fit View" [ref=e270] [cursor=pointer]
              - img "Mini Map" [ref=e274]
            - textbox "Search nodes" [ref=e282]:
              - /placeholder: Find a node…
            - generic [ref=e283]:
              - button "Auto layout" [ref=e284] [cursor=pointer]
              - button "Collapse" [ref=e289] [cursor=pointer]
              - button "Path actions" [ref=e294] [cursor=pointer]
            - generic [ref=e298]:
              - generic [ref=e299]: Execution
              - generic [ref=e301]: Source / reference
              - generic [ref=e303]: rev 4
          - generic [ref=e305]:
            - button "Runs 0" [ref=e306] [cursor=pointer]:
              - text: Runs
              - generic [ref=e310]: "0"
            - button "Terminal" [ref=e311] [cursor=pointer]
            - button "Toggle run panel" [ref=e315] [cursor=pointer]
      - separator "Resize analysis panel" [ref=e318]
      - complementary "Research analysis panel" [ref=e319]:
        - generic [ref=e320]:
          - tablist [ref=e322]:
            - tab "Analysis" [selected] [ref=e323] [cursor=pointer]
            - tab "Paper" [ref=e324] [cursor=pointer]
          - button "Pin analysis panel" [ref=e325] [cursor=pointer]
          - button "Close analysis panel" [ref=e328] [cursor=pointer]
        - tabpanel "Analysis" [ref=e332]:
          - generic [ref=e333]:
            - generic [ref=e334]:
              - heading "Research analysis" [level=2] [ref=e335]
              - paragraph [ref=e336]: Read saved experiments, issues and sources, then request model analysis when needed.
            - tablist [ref=e338]:
              - tab "Project" [ref=e339] [cursor=pointer]
              - tab "Node" [selected] [ref=e340] [cursor=pointer]
              - tab "Path" [ref=e341] [cursor=pointer]
            - generic [ref=e342]:
              - generic [ref=e343]:
                - generic [ref=e344]: Analysis node
                - combobox "Analysis node" [ref=e345]:
                  - option "Choose a saved node"
                  - option "Alpha analysis node · r1" [selected]
                  - option "Beta analysis node · r0"
              - button "Edit selected node" [ref=e346] [cursor=pointer]
            - region "Saved analysis context" [ref=e347]:
              - strong [ref=e348]: Alpha analysis node
              - paragraph [ref=e349]: Saved project revision 4 · 1 nodes · 0 runs
              - paragraph [ref=e350]:
                - code [ref=e351]: c9368579-39da-5c44-b00e-d416c70f3cac
                - text: · implementation · r1 ·
                - generic [ref=e352]: idle
                - generic [ref=e354]: not_evaluated
              - group [ref=e356]:
                - generic "Goal and context coverage" [ref=e357] [cursor=pointer]
              - group [ref=e358]:
                - generic "Actual execution and result records (0)" [ref=e359] [cursor=pointer]
              - group [ref=e360]:
                - generic "Recorded path health" [ref=e361] [cursor=pointer]
              - group [ref=e362]:
                - generic "Paths, nodes and dependency details" [ref=e363] [cursor=pointer]
                - article [ref=e364]:
                  - strong [ref=e365]: Main
                  - paragraph [ref=e366]:
                    - generic [ref=e367]: active
                    - text: · 2 nodes · 0 runs
                  - link [ref=e369] [cursor=pointer]:
                    - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?analysis=1&analysis_scope=branch&branch=354a5ab5-d49d-40e2-bc90-219655db32c0
                    - code [ref=e370]: 354a5ab5-d49d-40e2-bc90-219655db32c0
                  - generic [ref=e371]: "{}"
                - group [ref=e372]:
                  - generic "Alpha analysis node · r1" [active] [ref=e373] [cursor=pointer]
                  - link [ref=e375] [cursor=pointer]:
                    - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?analysis=1&analysis_scope=node&node=c9368579-39da-5c44-b00e-d416c70f3cac
                    - text: Node
                    - code [ref=e376]: c9368579-39da-5c44-b00e-d416c70f3cac
                  - paragraph [ref=e377]:
                    - generic [ref=e378]: idle
                    - generic [ref=e380]: not_evaluated
                    - generic [ref=e382]: draft
                  - paragraph [ref=e384]: Alpha analysis node saved instructions
                  - heading "Upstream dependencies" [level=4] [ref=e385]
                  - link [ref=e387] [cursor=pointer]:
                    - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?analysis=1&analysis_scope=node&node=e5bee3b1-dd09-5649-b289-30373309092c
                    - text: Node
                    - code [ref=e388]: e5bee3b1-dd09-5649-b289-30373309092c
                  - heading "Downstream nodes" [level=4] [ref=e389]
                  - link [ref=e391] [cursor=pointer]:
                    - /url: /projects/ba8fcefe-cd2d-4b0f-96e0-63cee0b42694/workspace?analysis=1&analysis_scope=node&node=e5bee3b1-dd09-5649-b289-30373309092c
                    - text: Node
                    - code [ref=e392]: e5bee3b1-dd09-5649-b289-30373309092c
                  - group [ref=e393]:
                    - generic "Complete configuration, inputs, outputs and comments" [ref=e394] [cursor=pointer]
                - group [ref=e395]:
                  - generic "Complete saved graph and dependencies" [ref=e396] [cursor=pointer]
              - group [ref=e397]:
                - generic "Tool execution, events and file versions" [ref=e398] [cursor=pointer]
              - group [ref=e399]:
                - generic "Papers, literature and research resources" [ref=e400] [cursor=pointer]
            - generic [ref=e401]:
              - generic [ref=e402]:
                - generic [ref=e403]: Analysis question
                - textbox "Analysis question" [ref=e404]:
                  - /placeholder: Which experiments, issues or next steps should be examined?
              - group [ref=e405]:
                - generic "Literature search" [ref=e406] [cursor=pointer]
              - paragraph [ref=e407]: No model is connected for analysis. Saved context remains readable.
              - link "Connect a model" [ref=e408] [cursor=pointer]:
                - /url: /settings
              - paragraph [ref=e409]: Draft project revision 4
              - generic [ref=e410]:
                - button "Use current context" [ref=e411] [cursor=pointer]
                - button "Request AI analysis" [disabled] [ref=e417]
            - group [ref=e421]:
              - generic "Analysis history for this scope (0)" [ref=e422] [cursor=pointer]
```

# Test source

```ts
  121 |           citation_ids: ["fixture-citation-full-id"],
  122 |         },
  123 |       ],
  124 |       limitations: ["Authored frontend protocol fixture; no model call."],
  125 |       coverage: { node_ids: [node.id], run_ids: [], resource_ids: [] },
  126 |     },
  127 |     citations: [
  128 |       {
  129 |         id: "fixture-citation-full-id",
  130 |         title: `Explicit metadata fixture ${projectId}`,
  131 |         authors: ["Fixture author"],
  132 |         year: 2026,
  133 |         source: "crossref",
  134 |         read_scope: "metadata/abstract",
  135 |         url: "https://example.org/analysis-fixture",
  136 |         abstract: "Authored browser regression metadata.",
  137 |       },
  138 |     ],
  139 |     literature_searches: [
  140 |       { query: "fixture", source: "crossref", status: "not_run", count: 1 },
  141 |     ],
  142 |     artifacts: {
  143 |       context: `runs/${id}/analysis_input.json`,
  144 |       report: `runs/${id}/research_analysis.json`,
  145 |     },
  146 |   };
  147 | }
  148 | 
  149 | test("actual analysis preview follows full node/path URLs and retains independent drafts without model calls", async ({
  150 |   page,
  151 |   request,
  152 | }) => {
  153 |   const { project, alpha, beta } = await fixture(request);
  154 |   for (const change of [
  155 |     {
  156 |       operation: "edit_node",
  157 |       targets: [alpha.id],
  158 |       params: {
  159 |         inputs: [{ node_id: beta.id, path: "saved-source-fixture.txt" }],
  160 |       },
  161 |     },
  162 |     {
  163 |       operation: "add_dependency",
  164 |       targets: [],
  165 |       params: { source: alpha.id, target: beta.id, relation: "supports" },
  166 |     },
  167 |   ]) {
  168 |     const graph = await (
  169 |       await request.get(`/api/projects/${project.id}/graph`)
  170 |     ).json();
  171 |     const changed = await request.post(
  172 |       `/api/projects/${project.id}/graph/commands`,
  173 |       {
  174 |         data: {
  175 |           request_id: crypto.randomUUID(),
  176 |           expected_revision: graph.revision,
  177 |           ...change,
  178 |           run: false,
  179 |         },
  180 |       },
  181 |     );
  182 |     expect(changed.ok()).toBeTruthy();
  183 |   }
  184 |   let posts = 0;
  185 |   page.on("request", (request) => {
  186 |     if (
  187 |       request.method() === "POST" &&
  188 |       /research\/analysis$/.test(request.url())
  189 |     )
  190 |       posts++;
  191 |   });
  192 |   try {
  193 |     const before = await (
  194 |       await request.get(`/api/projects/${project.id}/graph`)
  195 |     ).json();
  196 |     await page.goto(
  197 |       `/projects/${project.id}/workspace?analysis=1&analysis_scope=node&node=${alpha.id}&demo=1`,
  198 |     );
  199 |     await expect(
  200 |       context(page).getByText(alpha.id, { exact: true }).first(),
  201 |     ).toBeVisible();
  202 |     await context(page)
  203 |       .getByText("Paths, nodes and dependency details", { exact: true })
  204 |       .click();
  205 |     await context(page)
  206 |       .getByText(/^Alpha analysis node · r\d+$/)
  207 |       .click();
  208 |     const alphaDetails = context(page)
  209 |       .locator("details")
  210 |       .filter({
  211 |         has: page.locator(":scope > summary", {
  212 |           hasText: /^Alpha analysis node · r\d+$/,
  213 |         }),
  214 |       });
  215 |     const alphaDependencies = alphaDetails.locator(
  216 |       "h4 + .analysis-bound-references",
  217 |     );
  218 |     await expect(alphaDependencies.nth(0).getByRole("link")).toContainText(
  219 |       beta.id,
  220 |     );
> 221 |     await expect(alphaDependencies.nth(1).getByRole("link")).toHaveCount(0);
      |                                                              ^ Error: expect(locator).toHaveCount(expected) failed
  222 |     await expect(
  223 |       pane(page).getByRole("button", { name: "Request AI analysis" }),
  224 |     ).toBeDisabled();
  225 |     await pane(page)
  226 |       .getByLabel("Analysis question")
  227 |       .fill("Retained Alpha question");
  228 |     await expect(page.locator("#node-inspector")).toBeHidden();
  229 |     await pane(page).getByLabel("Analysis node").selectOption(beta.id);
  230 |     await expect(
  231 |       context(page).getByText(beta.id, { exact: true }).first(),
  232 |     ).toBeVisible();
  233 |     await context(page)
  234 |       .getByText("Paths, nodes and dependency details", { exact: true })
  235 |       .click();
  236 |     await context(page)
  237 |       .getByText(/^Beta analysis node · r\d+$/)
  238 |       .click();
  239 |     const betaDetails = context(page)
  240 |       .locator("details")
  241 |       .filter({
  242 |         has: page.locator(":scope > summary", {
  243 |           hasText: /^Beta analysis node · r\d+$/,
  244 |         }),
  245 |       });
  246 |     const betaDependencies = betaDetails.locator(
  247 |       "h4 + .analysis-bound-references",
  248 |     );
  249 |     await expect(betaDependencies.nth(0).getByRole("link")).toHaveCount(0);
  250 |     await expect(betaDependencies.nth(1).getByRole("link")).toContainText(
  251 |       alpha.id,
  252 |     );
  253 |     await expect(pane(page).getByLabel("Analysis question")).toHaveValue("");
  254 |     await pane(page)
  255 |       .getByLabel("Analysis question")
  256 |       .fill("Retained Beta question");
  257 |     await page.goBack();
  258 |     await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
  259 |       "Retained Alpha question",
  260 |     );
  261 |     await page.goForward();
  262 |     await page.reload();
  263 |     await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
  264 |       "Retained Beta question",
  265 |     );
  266 |     await pane(page).getByRole("tab", { name: "Project", exact: true }).click();
  267 |     await expect(
  268 |       pane(page).getByRole("tab", { name: "Project", exact: true }),
  269 |     ).toHaveAttribute("aria-selected", "true");
  270 |     await page
  271 |       .locator(".react-flow__node")
  272 |       .filter({ hasText: "Alpha analysis node" })
  273 |       .click();
  274 |     await expect(
  275 |       pane(page).getByRole("tab", { name: "Node", exact: true }),
  276 |     ).toHaveAttribute("aria-selected", "true");
  277 |     await expect(pane(page).getByLabel("Analysis question")).toHaveValue(
  278 |       "Retained Alpha question",
  279 |     );
  280 |     await pane(page).getByRole("tab", { name: "Path", exact: true }).click();
  281 |     await pane(page).getByLabel("Analysis path").selectOption(alpha.branch_id);
  282 |     await expect(
  283 |       context(page).getByText(alpha.branch_id, { exact: true }).first(),
  284 |     ).toBeVisible();
  285 |     await page.reload();
  286 |     await expect(pane(page).getByLabel("Analysis path")).toHaveValue(
  287 |       alpha.branch_id,
  288 |     );
  289 |     expect(posts).toBe(0);
  290 |     expect(
  291 |       await (await request.get(`/api/projects/${project.id}/graph`)).json(),
  292 |     ).toEqual(before);
  293 |   } finally {
  294 |     await request.delete(`/api/projects/${project.id}`);
  295 |   }
  296 | });
  297 | 
  298 | test("uncertain analysis transport replays original UUID/fingerprint and preserves edits made before acknowledgment", async ({
  299 |   page,
  300 |   request,
  301 | }) => {
  302 |   const { project, alpha } = await fixture(request);
  303 |   const actual = await (
  304 |     await request.get(
  305 |       `/api/projects/${project.id}/research/analysis/context?scope=node&node_id=${alpha.id}`,
  306 |     )
  307 |   ).json();
  308 |   const requests: any[] = [],
  309 |     runId = crypto.randomUUID();
  310 |   let release!: () => void;
  311 |   const held = new Promise<void>((resolve) => (release = resolve));
  312 |   await page.route(
  313 |     `**/api/projects/${project.id}/research/analysis/context?*`,
  314 |     async (route) => {
  315 |       const response = await route.fetch();
  316 |       const value = await response.json();
  317 |       await route.fulfill({
  318 |         response,
  319 |         json: {
  320 |           ...value,
  321 |           provider: {
```