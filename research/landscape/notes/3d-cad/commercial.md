# Commercial AI agents and products for 3D modeling and mechanical CAD (state as of 2026-10-24)

Method note: about 18 searches and fetches, run on 2026-10-24. Many search results came back as model-summarized snippets. Where I fetched the primary page I say so. Vendor-run "comparisons" (Meshy, Neural4D, Leo AI blog, texocad blog) are marked as biased.

## Parametric/B-rep CAD AI startups: who actually runs an agent loop that edits feature trees?

### Takeaway
Only a few startups have shipped, verified agent loops that create or edit editable parametric models. Zoo's Zookeeper writes and edits KCL code and checks its own work with snapshots. Adam edits feature trees inside Onshape and Fusion. Leo AI generates assemblies with feature trees across SolidWorks, Onshape, CATIA and Inventor. Backflip and Spectral are mainly mesh/scan/image-to-B-rep converters rather than agents that edit trees. Hestus is an in-CAD automation copilot with few public specifics.

### Cited Findings
**Zoo (Zookeeper / Text-to-CAD / Design Studio)**
- Zookeeper launched on 2026-02-05 as a "conversational CAD agent". It is "an expert in KittyCAD Language (KCL)", Zoo's open-source code-CAD language and native project format. It can build new models in an empty project and modify existing ones. Users can select bodies, faces or edges to direct edits (for example, rounding selected edges). It "can view the model from any angle" and by default takes snapshots to review and validate its own changes. It can also search the web for datasheets, standards and material specs. Primary page fetched: [Zoo blog](https://zoo.dev/blog/announcing-zookeeper)
- Agent loop architecture: the agent writes code, regenerates geometry, renders a snapshot and self-checks. The feature "tree" here is a KCL program, not a SolidWorks-style history tree. The launch post does not describe how many iterations it runs or how it recovers from failures. [Zoo blog](https://zoo.dev/blog/announcing-zookeeper)
- Import formats: Zoo Design Studio imports STL and STEP, and Zookeeper can read and assemble them. The launch post does not state export formats. [Zoo blog](https://zoo.dev/blog/announcing-zookeeper)
- Pricing at launch, 2026-02-05:
  - Free includes $10/month of Zookeeper credit, then $0.50/min.
  - Plus is $20/month and includes about 400 min.
  - Pro is $99/month with unlimited Zookeeper in Design Studio.
  - Source: [Zoo blog](https://zoo.dev/blog/announcing-zookeeper)
- A September 2026 third-party review adds a Team tier at $399/user/month. It says the Free and Plus tiers allow training on user data unless the user opts out, while Pro and Team exclude user data by default. It says API and MCP activity is metered separately even on Pro. [texocad review](https://blog.texocad.ai/posts/zoo-text-to-cad-review); see also [Zoo pricing](https://zoo.dev/zoo-pricing) (not fetched).

**Adam (adam.new, YC W25)**
- Adam's product line, from the homepage fetched 2026-10-24:
  - Adam Copilot, an "AI CAD copilot for Onshape and Autodesk Fusion".
  - CADAM, an open-source text-to-CAD tool.
  - "GPT-6 Astra for CAD", which runs inside SolidWorks, Onshape and Fusion.
  - Connector plug-ins for Fusion and Rhino.
  - A Developer Platform with a REST API, an MCP server and OAuth.
  - Source: [adam.new](https://adam.new/)
- The Copilot page says it edits parts from prompts and uses the selected geometry as context. It also "turns rough CAD into parametric models". The page does not explicitly say "feature tree" or describe pricing. [adam.new/copilot](https://adam.new/copilot)
- Onshape's own blog confirms feature-tree editing. Adam AI is listed on the Onshape App Store as a free open-beta app. It works inside Part Studios to clean up feature trees: it extracts hardcoded values into variables, renames features and merges redundant operations. Onshape calls the integration Beta. [Onshape blog](https://www.onshape.com/en/blog/adam-ai-app-store-cad-co-pilot); also [Onshape partners blog](https://www.onshape.com/en/blog/cloud-native-cad-partners-simscale-luminary-cloud-leo-adam-ai)
- A search snippet says Adam's harness "won't merge features if that would break the model" and has memory only within a single conversation. The snippet appears to come from a Show HN post, "AI CAD Harness". I did not fetch it. [HN](https://news.ycombinator.com/item?id=47977694)
- A third-party review reports $4.1M raised. It lists text-to-CAD pricing as a limited free plan, Standard at $5.99/month and Pro at $17.99/month. These figures are unverified on the vendor site. [pasqualepillitteri.it](https://pasqualepillitteri.it/en/news/3372/adamcad-text-to-cad-ai-review-2026)
- Verdict on the earlier "unverified" claim: Onshape itself confirms feature-tree editing in Onshape. Fusion editing is claimed on adam.new. SolidWorks support appears only under the "GPT-6 Astra" page, not the Copilot.

**Leo AI**
- Leo generates full CAD assemblies from a text prompt, with "complete part and feature tree". The example was a hydraulic piston for a 50 kg load with a 100 mm stroke, built from the user's parts inventory. The agent does the design calculations, searches internal and online parts, and assembles them. It works with SolidWorks, Onshape, CATIA and Inventor. Mates are still manual: "Parts aren't mated automatically yet." The article is undated, with images from about March 2026. Its only quality evidence is the CEO's claims (engineers at Nvidia, HP and Intel use it); there is no independent validation. [Engineering.com](https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/)
- Funding: a $5M seed led by Flint Capital in September 2025 brought the total to $9.7M. [Leo blog](https://www.getleo.ai/blog/leo-ai-raises-9-7m-to-build-the-world-s-first-ai-for-mechanical-engineering) Crunchbase lists $5.0M in one round. [Crunchbase](https://www.crunchbase.com/organization/leo-ai-81b4)
- Users: more than 60,000. [Machine Design](https://www.machinedesign.com/automation-iiot/article/55330891/leo-ai-leo-ai-how-cad-aware-ai-is-changing-mechanical-design-and-engineering-workflows)
- Leo is also an Onshape AI partner. [Onshape partners blog](https://www.onshape.com/en/blog/cloud-native-cad-partners-simscale-luminary-cloud-leo-adam-ai)

**Backflip AI**
- Backflip is based in San Francisco. It has raised $30M over 2 rounds, with investors including a16z and NEA. [Tracxn](https://tracxn.com/d/companies/backflipai/__geoWQBhwm2f837EFQgZ1xmVQv5WR6UGwXCHKPTHG1-8)
- In August 2026 it launched a scan-to-CAD model that converts 3D scans and mesh files into parametric CAD. It runs inside Autodesk Fusion. [mlq.ai](https://mlq.ai/news/backflip-ai-launches-scan-to-cad-tool-inside-autodesk-fusion/); [tryplox](https://www.tryplox.com/news/backflip-ai-3d-scans-editable-cad-models)
- Sources conflict on the founding date (2020 vs December 2024).

**Hestus**
- Hestus announced a $7.4M seed on 2026-10-02, led by Insight Partners. With a $1.5M pre-seed, its total is $8.9M. It works inside existing CAD to automate repetitive design work and DFM evaluation. It claims more than 5,000 engineers use it. [Unite.ai](https://www.unite.ai/hestus-raises-7-4-million-seed-to-bring-ai-into-mechanical-engineering/); [Made With Models](https://madewithmodels.com/news/2026-10-04/hestus-74m-mechanical-ai/)
- Dealroom gives a conflicting $4.79M figure. [Dealroom](https://dealroom.co/news/158635-hestus-raises-7-4m-seed-to-bring-ai-into-mechanical-engineering/)

**Spectral Labs (SGS-1)**
- SGS-1 is a "foundation model for parametric 3D parts". It takes an image or a mesh as input and outputs B-rep geometry as STEP. Its uses include converting scans, STL or mesh files to parametric STEP for reverse engineering, and generating geometry in an assembly context. Investors include Pebblebed. [Pebblebed](https://pebblebed.com/portfolio/spectral)
- A public Hugging Face demo downloads STEP files. Its guidance says to avoid game art, organic shapes and very thin designs. [HF Space](https://spectral-labs-sgs-1.hf.space)
- The vendor claims SGS-1 beat GPT-5 and HoLa-BRep on a 75-image test set. Reported limits are organic surfaces and full assemblies. This comes only from a secondary summary, with no independent evaluation found. [pytorch.kr](https://discuss.pytorch.kr/t/gn-sgs-1-cad-the-first-generative-model-for-structured-cad/7824)

**Rhino ecosystem (third-party agents)**
- Reer is a Rhino plugin described as "a true AI collaborator directly into Rhino" (May 2026). It works in the cloud, asks before acting, and everything it does can be undone with Ctrl+Z. [Rhino blog](https://blog.rhino3d.com/2026/05/reer-true-ai-collaborator-directly-into.html)
- Raven calls itself "the intelligence layer for CAD" for Rhino and Grasshopper. [raven.build](https://www.raven.build/en)
- ShapeDiver released "Agent Skills" for coding agents (July 2026). [Rhino blog](https://blog.rhino3d.com/2026/07/supercharge-your-coding-agents.html)
- neospline.ai is a Rhino 8 agent plugin that makes changes using the same tools a user would. It is a forum post seeking feedback. [McNeel forum](https://discourse.mcneel.com/t/i-built-ai-agent-working-natively-inside-rhino-seeking-feedback/217438)

### Inferences
- Agent loops that edit feature trees exist in three forms:
  - **Code-CAD:** Zoo writes KCL and checks it against snapshots.
  - **API-driven edits of native trees:** Adam in Onshape and Fusion, and Leo across several CAD systems.
  - **Supervised plugins:** Reer and neospline in Rhino, which is NURBS, not a history tree.
- Spectral SGS-1 and Backflip are "geometry translators" (image, mesh or scan to B-rep). They are not agents that edit trees, though they could serve as tools inside an agent.
- No vendor publishes an independent success-rate benchmark on real engineering parts. Quality evidence is limited to demos, CEO claims and user counts.

### Gaps
- **Not found at all:** Henqo, Infinitform, CADAgent (as a company) and "Gencad". These names may be very small or misremembered.
- **Kittl:** Kittl is, to my knowledge, a graphic design tool. It is not CAD; I did not verify this.
- **Shapr3D:** no evidence found of a shipped AI agent.
- **nTop:** described only as mature generative/implicit design for additive manufacturing. No AI-agent product was found. [cadguide](https://cadguide.tools/best/feature/ai-assisted) is a weak source.
- **Unverified details:** Adam's current pricing and funding are not confirmed on its own site. Zoo's total funding was not researched in this pass. Backflip's product output formats (native Fusion features vs STEP) were not confirmed.

## Incumbents: Autodesk, Dassault, PTC, Siemens, McNeel

### Takeaway
By late 2026, Autodesk is furthest along: Assistant is GA in Fusion, it has MCP servers and neural-CAD text-to-editable-geometry, and it is planned as a standalone cross-product agent in 2027. Dassault's Leo companion demos sketch-to-parametric-model plus FEA. PTC Onshape's AI Advisor is still a documentation assistant; agents that edit geometry are a roadmap item, but a FeatureScript MCP server has shipped. Siemens NX Copilot is mostly documentation chat. There is no Rhino 9.

### Cited Findings
**Autodesk**
- At AU 2026 (September 2026), Autodesk Assistant became generally available to all Fusion users. Autodesk added new MCP servers and agentic experiences for Fusion Manage. [Architosh](https://architosh.com/2026/09/au2026-key-announcements-in-agentic-ai-forma-and-fusion/); [Engineering.com](https://www.engineering.com/au-2026-autodesks-ai-assistant-is-evolving/)
- DEVELOP3D reports that a text prompt in Fusion's Assistant ("Generate an air fryer") yields "high quality" editable 3D CAD geometry from neural CAD models, one of which targets prismatic parts. It calls this a big leap from Project Bernini, Autodesk's 2024 research model for text, image or point-cloud to 3D shape that was not commercial. [DEVELOP3D](https://develop3d.com/cad/autodesk-shows-its-ai-hand/)
- CEO Andrew Anagnost: "Autodesk Assistant will be available as a standalone agentic first experience, one that reaches across products." Coverage says it will orchestrate across Forma, Fusion and Flow, with debut expected in 2027. Pricing is unresolved. [Architosh](https://architosh.com/2026/10/au2026-autodesk-shifts-ai-from-chatbots-to-agentic-ecosystems/)
- Autodesk's official AU 2026 news (2026-09-15, fetched) lists these features:
  - Fusion **AutoTimeline**, which "converts imported geometry into editable parametric parts".
  - **AutoAssemble**.
  - **System Modeler** for machine setup, toolpaths and production prep.
  - An agentic PLM experience.
  - In Inventor, Assistant uses the APIs and iLogic to analyze models, modify parameters and components, create model states and automate tasks.
  - Source: [Autodesk News](https://adsknews.autodesk.com/en/news/autodesk-ai-design-manufacturing-au-2026/)
- Autodesk posted "Changes to Autodesk Assistant in Fusion beginning October 6th". I did not fetch it. [Fusion blog](https://www.autodesk.com/products/fusion-360/blog/changes-to-autodesk-assistant-in-autodesk-fusion-beginning-october-6th/)

**Dassault Systèmes (SolidWorks / 3DEXPERIENCE)**
- **Aura** launched in February 2025 as the first "Virtual Companion". It is trained on SolidWorks guides and help docs and is essentially a knowledge and search assistant. [DEVELOP3D](https://develop3d.com/cad/solidworks-aura-annonces-as-new-ai-virtual-companion/)
- 3DEXPERIENCE World, February 2026 (fetched):
  - **Leo** covers engineering, simulation and manufacturing, due mid-2026. In a live xDesign demo it turned drawings into parametric sketches, used Sketch-to-3D to make a parametric model, and ran FEA from a prompt using a surrogate model.
  - **Marie** covers materials and chemistry, due later in 2026.
  - The companions are built on Mistral models and hosted on Outscale.
  - No pricing was given, and the article does not say whether the agents edit feature trees.
  - Source: [DEVELOP3D](https://develop3d.com/cad/new-solidworks-ai-agents-added-at-3dexperience-world/); [BusinessWire](https://www.businesswire.com/news/home/20260210798252/en/Dassault-Systmes-Unveils-a-New-Way-of-Working-for-Industry-with-AI-Powered-Virtual-Companions)
- A Dassault community wiki says Aura, Leo and Marie are "now globally available on 3DEXPERIENCE SaaS". [3DSwym](https://3dswym.3dexperience.3ds.com/wiki/3dexperience-platform-user-s-community/solidworks-what-s-new-generative-experiences-virtual-companions_vKtVfw5jT1S24M1Hh9sqdQ)

**PTC Onshape / Creo**
- Onshape's AI blog (fetched) says:
  - **Shipped:** AI Advisor, which gives real-time help and troubleshooting in the design environment.
  - **Beta:** Replicate Annotations for drawings.
  - **Announced:** LLM-powered FeatureScript, AI model search and AI Quick Render.
  - **In development:** "AI agents that can be added to projects like any other teammate", with revocable permissions and tracked, reversible actions. Planned abilities are fixing failing features, updating parameters and modifying geometry. An Onshape MCP server is described in the future tense.
  - Source: [Onshape blog](https://www.onshape.com/en/blog/ai-artificial-intelligence-cloud-native-cad-pdm-platform)
- Reviewers say AI Advisor does not generate geometry or execute commands. The same source says a FeatureScript MCP server reached GA in August 2026 after Onshape Labs opened in July 2026. This comes from a competitor blog. [Leo AI blog](https://www.getleo.ai/blog/onshape-built-in-ai); [DEVELOP3D](https://develop3d.com/cad/built-differently-onshapes-ai-advisor/)
- An official Onshape MCP server exposing FeatureScript tools (create/test features, search docs) exists in this research environment. This is consistent with the MCP-server claim (observation, not a citation).
- No Creo-specific AI agent information was found.

**Siemens**
- NX Design Copilot is described as a natural-language chat that searches NX documentation. Teamcenter Copilot is separate. One source says an "NX AI Chat" is in development with limited availability. [texocad](https://blog.texocad.ai/posts/siemens-nx-ai-chat); [Leo AI comparison](https://www.getleo.ai/blog/built-in-ai-cad-plm-comparison-2026)
- Reviewers' overall view is that Aura, Onshape AI Advisor and NX Copilot "can't see your model". This comes from a biased competitor source. [Leo AI comparison](https://www.getleo.ai/blog/built-in-ai-cad-plm-comparison-2026)

**McNeel Rhino**
- There is no Rhino 9 release. The latest stable version is Rhino 8.33 (2026-07-07). AI work in Rhino is third-party (see above). [Wikipedia](https://en.wikipedia.org/wiki/Rhinoceros_3D)

### Inferences
- Incumbents are converging on two pieces: an assistant with MCP or API access to the model, and neural "geometry to editable parametric" features (Autodesk AutoTimeline, Dassault Sketch-to-3D). These features directly threaten scan-to-CAD startups like Backflip.
- Onshape is most open to third-party agents (Adam, Leo, MCP server) but slowest on its own agents.

### Gaps
- Pricing for incumbent AI features: Autodesk Assistant, SolidWorks companions and NX Copilot.
- Siemens Solid Edge AI and Creo AI: no information found.
- How good Autodesk's neural CAD output is on real parts: only demo coverage was found.

## Mesh/asset generators used for printable models

### Takeaway
Meshy is the most commonly cited choice for print-ready (watertight, STL/3MF) output, and it is integrated into Bambu's MakerWorld. Tripo outputs raw meshes that often need repair. Rodin reviews are mixed. Hunyuan3D (open weights) has strong geometry but license limits. All of these produce meshes, not STEP. None offers an engineering agent loop.

### Cited Findings
- **Meshy** says it auto-repairs geometry to watertight meshes, exports STL and 3MF, and has a 97% slicer pass rate. These are internal, vendor numbers. [Meshy vs Tripo](https://www.meshy.ai/compare/meshy-vs-tripo); [Meshy blog](https://www.meshy.ai/blog/best-ai-tools-for-3d-printing)
- **Meshy on MakerWorld:**
  - Meshy's Image-to-3D was integrated into MakerWorld's MakerLab in March 2026. [smith3d](https://www.smith3d.com/bambu-lab-makerworld-unlock-ai-3d-model-generation-for-everyday-makers/)
  - Meshy-6 powers multi-color image-to-print on MakerWorld. [PR Newswire](https://www.prnewswire.com/news-releases/how-to-turn-any-image-into-a-full-color-3d-print-in-one-click--meshys-multi-color-printing-powered-by-meshy-6-is-now-live-on-makerworld-302714800.html)
- **Hunyuan3D (Tencent):** described as producing strong watertight detail but needing a large GPU. Its community license reportedly restricts use in the EU, UK and South Korea. [triposr.org](https://triposr.org/blog/image-to-3d-print); [Meshy vs Hunyuan3D](https://www.meshy.ai/compare/meshy-vs-hunyuan3d) (competitor)
- **Tripo:** good topology and speed, but raw meshes need cleanup for printing. [Neural4D](https://www.neural4d.com/features/neural4d-vs-tripo-vs-meshy-rodin) (competitor)
- **Tripo funding:** reportedly more than $50M from Alibaba and Baidu Ventures. [trellis2.app](https://trellis2.app/blog/best-ai-3d-model-generator) (weak source)
- **Rodin (Hyper3D):** a Meshy scorecard rates it low on watertightness. Neural4D says it suits SLA printing. Both sources are competitors. [Meshy blog](https://www.meshy.ai/blog/best-ai-tools-for-3d-printing); [Neural4D](https://www.neural4d.com/features/neural4d-vs-tripo-vs-meshy-rodin)
- **TRELLIS** appears in comparisons but I found no primary print-quality data. [trellis2.app](https://trellis2.app/blog/best-ai-3d-model-generator)

### Inferences
- For mechanical work, mesh generators fit cosmetic or figurine parts. Pipelines for functional parts would need mesh-to-B-rep (Spectral, Backflip, Autodesk AutoTimeline) or a native parametric generator.

### Gaps
- Sparc3D, Luma Genie and CSM: no 2026 information gathered.
- Independent watertightness benchmarks: none found. All comparisons found are vendor-authored.
- Meshy and Rodin funding: not found.

## Closing the loop with manufacturing (quoting, DFM, printing)

### Takeaway
The clearest consumer loop from generation to print is Bambu MakerWorld's MakerLab. It offers several image-to-3D engines, including Meshy 6, feeding Bambu Studio, though Bambu shut down some weaker AI tools in September 2026. On the engineering side, Leo claims an "idea to manufacturable assembly" loop, Hestus targets DFM, and Autodesk System Modeler automates CAM. I found no evidence of agentic quoting at Xometry, Protolabs or Hubs.

### Cited Findings
- **MakerLab engines:** as of 2026-08-17, MakerLab's Image-to-3D menu had eight choices across five engine families, including Meshy 6. [smith3d](https://www.smith3d.com/bambu-lab-makerworld-unlock-ai-3d-model-generation-for-everyday-makers/)
- **MakerLab billing:** since 2026-06-25, credits are charged at generation, not at export. [3Druck](https://3druck.com/en/diy/bambu-labs-makerworld-switches-billing-from-ai-tool-makerlab-to-generation-costs-39159552/)
- **MakerLab shutdowns:** Bambu shut down PrintMon Maker and AI Scanner on 2026-09-20 because they did not meet "community expectations for model quality". [3Druck](https://3druck.com/en/programs/makerworld-printmon-maker-and-ai-scanner-to-be-discontinued-by-bambu-lab-on-september-20-2026-30162323/)
- **MakerWorld listing rule:** in February 2026, MakerWorld began requiring that images in AI-generated model listings match the actual model. This was a response to misleading AI listings. [Fabbaloo](https://www.fabbaloo.com/news/makerworld-introduces-new-image-requirement-to-address-misleading-ai-generated-model-listings)
- **Leo:** CEO claims "the first time an AI has closed the loop from idea to manufacturable assembly". [Engineering.com](https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/)
- **Hestus:** helps engineers evaluate design for manufacturing. [Unite.ai](https://www.unite.ai/hestus-raises-7-4-million-seed-to-bring-ai-into-mechanical-engineering/)
- **Autodesk System Modeler:** covers machine setup, toolpath generation and production prep. [Autodesk News](https://adsknews.autodesk.com/en/news/autodesk-ai-design-manufacturing-au-2026/)
- **Dassault Leo:** described as covering "taking products to manufacture". [DEVELOP3D](https://develop3d.com/cad/new-solidworks-ai-agents-added-at-3dexperience-world/)
- **Xometry:** has a general AI quoting engine, discussed in trade press. No agentic product was found. [The Fabricator](https://www.thefabricator.com/thefabricator/article/shopmanagement/ai-dives-deep-into-the-manufacturing-marketplace)

### Inferences
- The loop into consumer printing is real, but its quality bar is low: Bambu retired its own AI tools for poor quality. In engineering, the loop to manufacturing is still claims plus CAM automation. No startup was found that goes from prompt to STEP to an instant quote to an order in one agent.

### Gaps
- Protolabs, Hubs and Xometry: no 2025–2026 agentic or AI-DFM product details were found within this pass's tool budget.
