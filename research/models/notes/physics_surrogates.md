# ML physics surrogates and AI-driven design optimization for Mises (as of 2026-10-24)

Context: Mises runs gmsh + CalculiX locally (static / thermal / modal), labels every result a "guide", re-runs on change with a coarse mesh first and refines in the background (design doc §7.5). Topology optimization, generative design and CFD are planned as paid cloud studies (§7.6). The free tier must cost close to nothing per user (§3 anti-goals, §11).

Research method: about 16 web searches and fetches on 2026-10-24. Items marked **[peer-reviewed]** are journal or conference papers. **[preprint]** means arXiv or a model card. **[vendor]** means marketing, a press release or a product page. **[secondary]** means an aggregator or a blog. Foundational papers from before 2024 are cited from prior knowledge with their canonical arXiv links and are marked **[background]**.

## 1. Surrogate model families: accuracy against FEA, generalization to unseen geometry, data needs

### Takeaway
Graph-network simulators (MeshGraphNets and its descendants) and transformer neural operators (Transolver, GeoTransolver, AB-UPT, GINO) are the leading families. On geometry like their training set they reach errors of a few percent. Accuracy drops sharply once shapes leave that distribution. Every published structural surrogate is trained on one part family (brackets, plates, beams) using hundreds to tens of thousands of FEA runs. None of them accepts any part a Mises user might draw.

### Cited Findings
- **[background]** MeshGraphNets (Pfaff et al., ICLR 2021) is the base graph-network simulator on simulation meshes. Most later structural GNN surrogates extend it — [arXiv 2010.03409](https://arxiv.org/abs/2010.03409)
- **[background]** Fourier Neural Operator (Li et al., 2020) — [arXiv 2010.08895](https://arxiv.org/abs/2010.08895). GINO (geometry-informed neural operator, 2023) pairs geometry processing with an FNO for large 3D PDEs — [arXiv 2309.00583](https://arxiv.org/abs/2309.00583). Transolver (ICML 2024) — [arXiv 2402.02366](https://arxiv.org/abs/2402.02366)
- **[preprint, Feb 2025]** Transolver++ targets "million-scale geometries". Its industrial tests used DrivAerNet++ and a new aircraft dataset, against 12 neural-operator baselines including GINO — [arXiv 2502.02414](https://arxiv.org/pdf/2502.02414)
- **[preprint, Feb 2026]** Transolver-3 handles volume fields of more than 160 million cells on DrivAerML. Older models needed chunked inference because their input capacity was exceeded — [arXiv 2602.04940](https://arxiv.org/pdf/2602.04940)
- **[preprint, Dec 2025]** CarBench (DrivAerNet++) reports relative L2 errors of AB-UPT 0.1358, Transolver 0.1503 and Transolver++ 0.1573. The newer Transolver++ did not beat Transolver — [arXiv 2512.07847](https://arxiv.org/pdf/2512.07847)
- **[preprint, May 2026]** RETO on DrivAerML gives surface pressure / velocity relative L2 of Transolver++ 0.127/0.133, Transolver 0.116/0.121, AB-UPT 0.102/0.124 and RETO 0.089/0.097 (self-reported). Even the state of the art sits at about 9–13% relative L2 on fields — [arXiv 2605.00062](https://arxiv.org/pdf/2605.00062)
- **[peer-reviewed, JCDE 2023]** Boundary-Oriented Graph Embedding (BOGE) reaches about 2.4–2.85% MAPE on stress fields, but only for a 2D cantilever-beam family — [JCDE](https://academic.oup.com/jcde/article/10/3/1026/7086117), [arXiv 2108.13509](https://arxiv.org/pdf/2108.13509)
- **[preprint, 2023]** A GNN surrogate for feature-based geometry design had about 9% median error on test set 1. Error rose above 40% on a harder second test set of shapes further from the training data. This is a direct measure of the generalization gap — [arXiv 2302.00557](https://arxiv.org/pdf/2302.00557)
- **[peer-reviewed, IASS 2024]** A MeshGraphNets variant predicts stress in steel plates with holes for unseen cases and generalizes across shapes and mesh resolutions, but only in 2D — [IASS 2024 paper 478](https://app.iass2024.org/files/IASS_2024_Paper_478.pdf)
- **[2025–26, ResearchGate]** "Mesh Graph Neural Network Framework for Accelerating FE Simulation for Arbitrary Geometries" extends MeshGraphNets to structural mechanics across varying geometries. Its error figures for fully unseen shapes were not found in what I read — [ResearchGate](https://www.researchgate.net/publication/406481156_Mesh_Graph_Neural_Network_Framework_for_Accelerating_Finite_Element_Simulation_for_Arbitrary_Geometries)
- **[preprint, Jun 2026]** A hybrid GNN–FEM approach (the GNN replaces only part of the domain and FEM keeps the physics) tracked full FEM on a crack-plus-hole geometry it had not seen. This "hybrid, physics-preserving" approach generalizes better than a pure surrogate — [arXiv 2606.19378](https://arxiv.org/pdf/2606.19378)
- **Structural datasets:**
  - SimJEB has 381 crowdsourced jet-engine bracket designs with structural simulations — [SimJEB](https://par.nsf.gov/biblio/10447940-simjeb-simulated-jet-engine-bracket-dataset)
  - DeepJEB (arXiv Jun 2024) is a synthetic bracket set derived from SimJEB. Models trained on it gained up to 23% R² and cut MAPE by more than 70% compared with traditional datasets. It is distributed through narnia.ai — [arXiv 2406.09047](https://arxiv.org/pdf/2406.09047)
  - DeepJEB++ is reported as 15,360 bracket designs with four load cases each, stress and displacement fields, on Hugging Face. The source is a promotional LinkedIn post and is unverified — [LinkedIn](https://kr.linkedin.com/in/jae-gil-lee-7007612b)
- **CFD datasets:**
  - **[background]** AirfRANS has 2D RANS airfoils — [arXiv 2212.07564](https://arxiv.org/abs/2212.07564)
  - **[background]** DrivAerNet++ has about 8k car designs — [arXiv 2406.09624](https://arxiv.org/abs/2406.09624)
  - DrivAerML is used for the high-fidelity automotive benchmarks above — [arXiv 2607.17297](https://arxiv.org/abs/2607.17297)
- **Heterogeneous data format:** PLAID (arXiv May 2025) is a unified data model for ML across heterogeneous physics simulations — [arXiv 2505.02974](https://arxiv.org/pdf/2505.02974)

### Inferences
- Mises parts are arbitrary user geometry: brackets, enclosures, mounts. They come with arbitrary fixtures and loads, and FDM material knock-downs. That is exactly the out-of-distribution case where the published error rises from about 9% to more than 40% (arXiv 2302.00557). A surrogate would need Mises's own training corpus, and that corpus can only come from running CalculiX.
- Automotive CFD is by far the most mature area (large public datasets, benchmarks, conformal UQ). Structural surrogates for general 3D parts are much less mature.
- Peak von Mises stress and safety factor are local hotspot quantities. Field-average metrics like relative L2 hide hotspot errors, and the hotspot is exactly what Mises reports.

### Gaps
- I found no peer-reviewed benchmark of 3D linear-elastic stress surrogates on truly diverse, unseen mechanical parts with peak-stress error reported.
- Thermal and modal surrogates for general parts: no specific results found. Siemens claims frequency extraction (see Q2).
- Exact training-set sizes needed for a given accuracy on structural parts were not established beyond the dataset sizes above.

## 2. Products and frameworks: access, pricing, open-source status, use by a small company

### Takeaway
NVIDIA PhysicsNeMo (Apache-2.0) is the only serious open-source framework. It is a toolkit for training your own surrogate, plus a few pretrained, mostly automotive-aero checkpoints. Commercial platforms (Ansys SimAI, PhysicsX, Neural Concept, Siemens Simcenter PhysicsAI, Monolith) are enterprise and quote-based, train on the customer's own simulation data, and have no published prices. None fits a near-zero-cost free tier or embedding in a third-party product.

### Cited Findings
- **NVIDIA PhysicsNeMo (formerly Modulus):**
  - Built on PyTorch and released under Apache 2.0 — [Rescale docs](https://rescale.com/documentation/ai-physics/), [NVIDIA open-source blog](https://developer.nvidia.com/blog/physics-ml-platform-physicsnemo-is-now-open-source). The NGC container has its own separate EULA — [NGC 26.05](https://catalog.ngc.nvidia.com/orgs/nvidia/physicsnemo/containers/physicsnemo/26.05)
  - The release notes list the following — [PhysicsNeMo 26.05 release notes](https://docs.nvidia.com/physicsnemo/26.05/release-notes/index.html):
    - v25.06: structural-mechanics MeshGraphNet for small deformations; DoMINO Automotive Aero NIM update
    - v25.03: DoMINO plus a DoMINO Automotive Aero NIM pretrained on a wide range of road-vehicle geometries
    - v25.08: mixture-of-experts external aero; DoMINO fine-tuning; DPOT (pretrained operator transformer)
    - v25.11 and v26.03: crash recipe with Transolver and MeshGraphNet; GeoTransolver for external aero
    - v26.05: notebooks evaluating checkpoints published on Hugging Face; Volume-of-Fluid underfill-flow surrogate; GeoTransolver/Globe aero recipe. PhysicsNeMo Sym has been merged into the core package.
  - The NGC catalog lists later tags 26.06 and 26.08 (26.08 dated 2026-08-27). I did not read those notes — [NGC listing](https://catalog.ngc.nvidia.com/orgs/nvidia/physicsnemo/containers/physicsnemo/-?_lr=1)
- **Ansys SimAI:**
  - No public pricing ("contact sales"). Two tiers: SimAI Pro, a desktop option for component-level AI on workstation GPUs, and SimAI Premium, the cloud-scale version. Each customer gets an isolated company workspace — [Ansys SimAI](https://ansys.com/products/simai)
  - Ansys ran a startup program from 2016. Whether it still runs or covers SimAI is unverified — [Ansys startup PR (2016)](https://ansys.com/news-center/press-releases/09-14-16-ansys-helps-startup-companies-create-tomorrows-products)
- **Siemens:**
  - Simcenter PhysicsAI 2026.1 adds sharper stress/strain hotspot prediction and automated frequency extraction for NVH — [Siemens blog](https://blogs.sw.siemens.com/simcenter/whats-new-in-simcenter-physicsai-2026-1/)
  - A May 2026 PhysicsAI add-on for CFD design exploration is built on STAR-CCM+ geometric deep learning — [Industrial Automation India](https://industrialautomationindia.in/news/siemens-launches-simcenter-physicsai-for-cfd)
  - Siemens closed its acquisition of Altair in March 2025, so the former Altair PhysicsAI now sits inside Siemens — [secondary market report](https://www.energent.ai/use-cases/en/compare/ai-solution-for-cae)
- **Neural Concept:**
  - Paid, quote-based. Integrates with NX, CATIA, SolidWorks and STAR-CCM+. Reported $100M Series C in Dec 2025 — [neuronfeed profile, secondary](https://neuronfeed.com/startups/neural-concept), [Siemens collaboration](https://eurekamagazine.co.uk/content/news/neural-concept-collaborates-with-siemens)
- **PhysicsX:**
  - Enterprise, quote-based. A profile lists free tools Ai.rplane and LGM-Aero. June 2026 Series C of about $300M at about $2.4B valuation; totals reported in conflicting amounts — [akta profile, secondary](https://akta.pro/company-profiles/00005j8/physicsx), [NGP](https://ngpenergy.com/news/physicsx-announces-300m-series-c-to-accelerate-physics-ai-for-industrial-engineering/)
- **Monolith AI:**
  - CoreWeave announced an agreement to acquire Monolith on 2025-10-06. Terms not disclosed; I found no confirmation that the deal closed — [CoreWeave IR](https://investors.coreweave.com/news/news-details/2025/CoreWeave-to-Acquire-Monolith-Expanding-AI-Cloud-Platform-into-Industrial-Innovation/default.aspx)
- **Autodesk Fusion generative design:**
  - Launched in 2020 at $1,000 a month or $8,000 a year — [Develop3D](https://develop3d.com/cad/fusion-360-changes-pricing-access-for-generative-design-tools/)
  - Later cut by about 80% to $1,600 a year ($200 a month), or about 33 cloud credits ($33) per generate-and-export study — [3D Printing Industry](https://3dprintingindustry.com/news/autodesk-slashes-price-of-generative-design-software-by-80-191183/), [Digital Engineering](https://www.digitalengineering247.com/article/generative-design-in-autodesk-fusion-360-now-more-accessible-and-flexible/)
  - A Sept 2026 secondary page quotes Fusion at $680 a year plus the extension at about $1,600 a year. Unverified — [cognitivefuture.ai](https://cognitivefuture.ai/ai-cad-software-pricing/)
- **nTop:**
  - Quote-based; no figures found — [cognitivefuture.ai](https://cognitivefuture.ai/ai-cad-software-pricing/)

### Inferences
- For Mises, PhysicsNeMo is the only option that can be legally embedded and shipped. Mises would have to generate the training data with CalculiX, train on GPUs (a one-off cloud cost) and ship an Apache-2.0-compatible model. The NGC container EULA should be avoided in favour of pip/source installs.
- The commercial platforms are "bring your own simulation data, train a per-product-family model" enterprise tools. They suit OEMs that iterate on one product line, not a hobby/pro CAD harness with arbitrary parts. They could at most be a later "bring your own license" integration, like the planned PyAnsys route in §7.6.
- The Autodesk credit price (about $33 per generative study) is a useful market reference for pricing a Mises paid study.

### Gaps
- No public pricing for SimAI, PhysicsX, Neural Concept, Simcenter PhysicsAI or nTop.
- No accuracy figures for SimAI or Simcenter PhysicsAI from an independent, non-vendor source.
- I did not read the PhysicsNeMo 26.06/26.08 notes, so newer pretrained checkpoints may exist.

## 3. Pretrained "physics foundation models" for structural or CFD use (2025–2026)

### Takeaway
No pretrained model released by October 2026 predicts stress, heat or modes for an arbitrary mechanical part out of the box. The "foundation models" that exist are domain-specific: car and SUV aero (DoMINO NIM, Luminary SHIFT), centrifugal pumps (SimScale), buildings (SeisGPT). The general-purpose ones are research models on 2D fluid benchmarks.

### Cited Findings
- **[peer-reviewed, Nature Communications, 2026]** SeisGPT is a physics-informed foundation model for building structural response. It was pretrained on 694 instrumented buildings plus more than 270k generated designs and reports below 5% normalized error and about 40,000× speedup over FEA on unseen buildings. It covers building structures, not mechanical parts — [Nature Comms](https://www.nature.com/articles/s41467-026-75508-5)
- **[preprint, Sep 2025]** "Towards a Physics Foundation Model" argues that one pretrained model can adapt to new physics through context alone. It notes that long-term stability must improve before engineering use — [arXiv 2509.13805](https://arxiv.org/html/2509.13805v4)
- **[preprint, Mar 2026]** WinDiNet fine-tunes a 2B-parameter video diffusion transformer on 13k 2D urban-wind CFD runs. It is differentiable, so it supports gradient-based design — [arXiv 2603.21210](https://arxiv.org/pdf/2603.21210)
- **[vendor]** NVIDIA DoMINO Automotive Aero NIM is pretrained on many road-vehicle geometries — [PhysicsNeMo release notes](https://docs.nvidia.com/physicsnemo/26.05/release-notes/index.html)
- **[vendor]** Luminary SHIFT-SUV, built with Honda and NVIDIA, uses DoMINO in PhysicsNeMo. It is announced as open source with a dataset growing from about 1k to 25k simulations. The 99-simulation sample dataset is CC-BY. The Hugging Face org showed "None public yet" for models when checked — [Luminary](https://www.luminarycloud.com/blog/introducing-luminary-shift-models-a-suite-of-physics-ai-foundation-models-to-transform-engineering-design/), [engineering.com](https://engineering.com/luminary-cloud-releases-open-source-physics-ai-model-with-honda-nvidia/), [HF](https://huggingface.co/luminary-shift/models)
- **[vendor]** SimScale and NVIDIA released a "first AI foundation model for pump simulation", trained on thousands of centrifugal-pump CFD runs — [SimScale](https://www.simscale.com/blog/the-first-ai-foundation-model-for-pump-simulation-with-nvidia/)
- **[preprint, 2025]** DPOT (Denoising Pre-trained Operator Transformer) is a pretrained PDE operator with samples in PhysicsNeMo v25.08 — [release notes](https://docs.nvidia.com/physicsnemo/26.05/release-notes/index.html)
- **[vendor, secondary]** PhysicsX LGM-Aero (a "large geometry model", aero) is listed as free to access. I found no details on its scope or license — [akta profile](https://akta.pro/company-profiles/00005j8/physicsx)

### Inferences
- External aero for cars is irrelevant to Mises's v1 structural loop. For a paid "wind tunnel" study on small parts (drone arms, bike mounts), a pretrained car-aero model would also be out of distribution. OpenFOAM-class solving stays the honest choice.
- If a general structural foundation model appears, the likely vehicles are PhysicsNeMo checkpoints on Hugging Face or a Luminary-style open release. Re-check before M-milestone planning.

### Gaps
- I found no pretrained, open structural (linear-elastic 3D) foundation model for mechanical parts. I could not rule out a release in the PhysicsNeMo 26.06–26.08 notes.
- No license or scope details for PhysicsX LGM-Aero.

## 4. ML for topology optimization and generative design, and conversion back to editable CAD

### Takeaway
Diffusion and flow-matching models can now produce near-optimal minimum-compliance topologies in about a second (TopoDiff, then OAT/OAT2 "Optimize Any Topology"). They still fail on a meaningful share of problems and are trained mainly on 2D or simple voxel domains, so a real solver-based optimizer plus FEA verification is still needed. Turning optimized density fields into editable B-rep/feature CAD is mostly a non-ML geometric-reconstruction problem. No mature ML solution exists, which matches Mises's plan to start "rough or manual".

### Cited Findings
- **[peer-reviewed, AAAI 2023]** TopoDiff (Mazé & Ahmed, MIT DeCoDE) is a conditional diffusion model with surrogate guidance for compliance and manufacturability. It beats GANs but samples slowly, about 21.6 s per topology against 0.06 s for TopologyGAN — [arXiv 2208.09591](https://arxiv.org/pdf/2208.09591), [project](https://decode.mit.edu/projects/topodiff/)
- **[NeurIPS 2025]** Optimize Any Topology (OAT) is a latent-diffusion U-Net over a neural-field autoencoder. It is the predecessor of OAT2 — [HF model card](https://huggingface.co/OpenTO/TopoDiT)
- **[preprint/model card, 2026]** TopoDiT (OAT2) is a 688.6M-parameter diffusion transformer trained with flow matching, in the latent space of the OpenTO NFAE. On a 5,000-problem OpenTO test split (zero-shot, no CFG) it reports a 21.8% single-sample failure rate (OAT: 40.0%) and 0.46% median compliance error (OAT: 3.94%). The results are self-reported, with no independent evaluation — [HF OpenTO/TopoDiT](https://huggingface.co/OpenTO/TopoDiT)
- **[peer-reviewed, JCDE Sep 2025]** Feng et al. reconstruct beam-like topology-optimization output into closed, editable parametric-surface CAD (B-spline lofting, blends at junctions). The method is not ML — [JCDE](https://academic.oup.com/jcde/article/12/9/162/8250036)
- **[peer-reviewed, DFX 2022]** Mayer et al. present feature-based B-rep reconstruction of non-beam-like topology-optimization results. Their premise: TO output is polygonal, while CAD needs analytic B-rep — [Design Society](https://www.designsociety.org/publication/45927/Feature-Based+Reconstruction+of+Non-Beam-Like+Topology+Optimization+Design+Proposals+in+Boundary-Representation)
- **[preprint, Apr 2026]** HiDiGen is hierarchical diffusion for B-rep generation. The authors note that precise CAD reconstruction "remains challenging" — [arXiv 2604.02847](https://arxiv.org/pdf/2604.02847)
- **[preprint, Jul 2026]** Masked topology modeling is self-supervised learning on parametric CAD, a possible feature backbone — [arXiv 2607.20642](https://arxiv.org/pdf/2607.20642)
- **[vendor pricing]** Autodesk generative design costs about $33 in credits per study (see Q2).

### Inferences
- In Mises's paid tier, a classical SIMP/level-set TO is the reliable core. Options are open-source TO on CalculiX or FEniCS in the cloud, possibly with an ML warm start such as OAT2-style generation followed by a few solver iterations. The solver must verify every candidate, and the high single-sample failure rate (about 22%) makes that verification mandatory.
- For "rebuild to editable features", the Mises agent tracing sketches plus extrude/loft over a skeletonized TO result is consistent with the state of the art. Skeleton-plus-loft methods (Feng 2025) are the most automatable for beam-like results.
- Check the OAT2 weights' license on the model card before any commercial use. I did not see the license field.

### Gaps
- No ML method found that converts TO density fields directly into Onshape-style parametric feature trees.
- No 3D, manufacturing-constrained (FDM overhang) ML-TO results with independent validation found.
- License of OpenTO/TopoDiT weights not confirmed.

## 5. ML for 3D-printing specifics: warping, failure, supports, anisotropic strength, print time

### Takeaway
The public data is either image-based failure detection (warping, stringing, spaghetti) for in-print monitoring, or small tabular studies predicting coupon strength from raster angle and print parameters. Neither predicts warping or strength for a given part geometry before printing. For pre-print use, slicer outputs (print time, supports) and physics-based knock-down factors remain more reliable than ML.

### Cited Findings
- **Warping image datasets:**
  - Roboflow "FDM Failures – Warping": 258 images plus a pretrained detector, CC BY 4.0 — [Roboflow](https://universe.roboflow.com/bu-engme500-vision-project/fdm-failures-warping)
  - Kaggle FDM defect dataset: 1,912 images in five classes, including 538 warping, MIT license — [Kaggle](https://www.kaggle.com/datasets/wengmhu/fdm-3d-printing-defect-dataset)
- **Warping detection systems:**
  - A closed-loop in-process warping detection system (CNN) pauses the print on warp detection — [Saluja et al. 2020](https://www.torontomu.ca/content/dam/frames/publications/Saluja_Xie_Fayazbakhsh_2020.pdf)
- **Anisotropic strength:**
  - **[peer-reviewed, 2025]** XGBoost predicts PLA tensile strength across raster angles with R² 0.9712, RMSE 2.14 MPa and MAPE 8.97% (coupon-level) — [Springer IJAMT](https://link.springer.com/article/10.1007/s00170-025-17127-w)
  - **[peer-reviewed, 2026]** Anisotropic impact strength of FDM composites, with 43 samples per orientation and data augmentation — [SAGE JTC](https://doi.org/10.1177/08927057261458720)
  - **[peer-reviewed, 2002, classic]** Ahn et al.: air gap and raster orientation strongly affect FDM tensile strength — [Rapid Prototyping J.](https://iss.mech.utah.edu/wp-content/uploads/sites/103/2012/10/Ahn-Anisotropic_material-2002.pdf)
- **Other:**
  - Porosity prediction for FDM with pretrained models — [PMC12525203](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12525203/)
  - **[preprint, Mar 2026]** LLMs as "tuning experts" for FDM print configuration selection — [arXiv 2603.22118](https://arxiv.org/pdf/2603.22118)

### Inferences
- For Mises §7.5: orientation-aware material models are better done physically (transversely isotropic CalculiX material with Z-direction knock-downs from filament data or literature) than with ML. The ML strength papers give coupon-level ratios that could inform the knock-down factors.
- For print time and supports: PrusaSlicer/Bambu Studio CLI slicing is exact and local. ML adds nothing here for v1.
- For printer connection (§7.7): the open warping/spaghetti detectors (CC-BY / MIT datasets) could later run on printer-camera frames locally at near-zero cost.

### Gaps
- No geometry-to-warping prediction model or dataset (pre-print) was found.
- No ML print-time predictor that beats slicer estimates was found, and I did not search this deeply.
- No dataset links part geometry, print orientation and measured part strength.

## 6. Practical recommendation for Mises v1, and how to stay honest that results are a guide, not a guarantee

### Takeaway
A learned surrogate is not worth it for v1. Arbitrary user parts are out of distribution for every available model. There is no pretrained structural model for general parts. A credible model would need a large CalculiX-generated corpus plus GPU training, and it would still need solver fallback. Coarse-mesh CalculiX, refined in the background, already gives "live" feedback on typical printed parts with known error behaviour. Surrogates make sense later, scoped per study: per-part design-space surrogates trained on the fly during generative/parametric sweeps in the paid cloud, wrapped in conformal uncertainty and verified with the solver.

### Cited Findings
- Generalization drops on out-of-distribution geometry, from about 9% to more than 40% median error — [arXiv 2302.00557](https://arxiv.org/pdf/2302.00557)
- State-of-the-art 3D operators still show about 9–16% relative L2 field error even on their own benchmark distribution — [CarBench](https://arxiv.org/pdf/2512.07847), [RETO](https://arxiv.org/pdf/2605.00062)
- **Conformal UQ for surrogates [preprint, Jul 2026]:**
  - Multi-granularity conformal prediction on GeoTransolver/Transolver (DrivAerML), targeting 90% coverage. Conformalized quantile regression gives case-level drag intervals. Residual-normalized calibration gives spatially adaptive field intervals.
  - Out-of-fold calibration cut coverage standard deviation from 10.41 to 3.10 percentage points. Point-adaptive calibration narrowed intervals by about 23–27%.
  - The method flags uncertain geometries and regions for follow-up CFD verification rather than switching to the solver automatically.
  - Source: [arXiv 2607.17297](https://arxiv.org/abs/2607.17297)
- Hybrid GNN–FEM that keeps FEM physics generalizes to unseen geometry better than a pure surrogate — [arXiv 2606.19378](https://arxiv.org/pdf/2606.19378)
- PhysicsNeMo (Apache-2.0) has a structural MeshGraphNet recipe for small deformations if Mises later trains its own — [release notes](https://docs.nvidia.com/physicsnemo/26.05/release-notes/index.html)

### Inferences
- **v1 (free, local):** Keep CalculiX as the single source of truth. Make "live" fast through engineering rather than ML:
  - second-order tetrahedra on a coarse mesh first, with refinement in the background
  - cached meshes and reuse of the factorization when only loads or materials change (environment × material matrix)
  - a mesh-convergence indicator: the change in peak stress between coarse and refined runs, shown as the result's error band
  - All of this costs nothing per user and fits the "guide" label honestly, because the error source (discretization) is known and measurable.
- **Paid tier, later:** During a generative or parametric sweep on one part, train a small per-part surrogate (a GP or a light MeshGraphNet/Transolver from PhysicsNeMo) on the first N cloud CalculiX runs. Use it to screen many variants, then re-solve the Pareto front with the real solver. This is the use case the commercial vendors actually serve, and it keeps the surrogate inside its training distribution.
- **Honesty rules if a surrogate is ever shown:**
  1. Label the result "surrogate estimate" separately from "solver guide".
  2. Show a conformal interval calibrated on held-out CalculiX runs for that study.
  3. Fall back to CalculiX automatically when the geometry's distance from training data (latent-space or feature distance) or the interval width passes a threshold.
  4. Never let an agent cite a surrogate number as a check it ran (anti-goal: "agents reporting checks they didn't run"). Record in the Step evidence whether the solver or the surrogate produced the number.
  5. Re-verify any design the agent proposes for merge or print with the solver.
- Cost: the free tier stays at zero ML inference cost. Surrogate training and inference happen only inside paid cloud jobs, gated server-side as in §11.

### Gaps
- No published measurement of coarse- against fine-mesh CalculiX error on typical FDM parts. Mises should benchmark this itself on a small part set (brackets, from SimJEB/DeepJEB geometry).
- No published, structural-specific conformal-UQ study for part stress surrogates was found. The available evidence is automotive CFD.
