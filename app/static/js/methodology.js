function svgIcon(name) {
        const common =
          'fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" viewBox="0 0 64 64"';
        const filled = 'viewBox="0 0 64 64" fill="currentColor"';
        const icons = {
          users: `<svg ${common}><circle cx="24" cy="22" r="8"/><circle cx="43" cy="24" r="7"/><path d="M10 48c2-10 10-16 20-16s18 6 20 16"/><path d="M35 39c3-5 8-8 15-7 6 1 10 6 11 14"/></svg>`,
          people: `<svg ${common}><circle cx="22" cy="20" r="7"/><circle cx="42" cy="20" r="7"/><circle cx="32" cy="34" r="8"/><path d="M7 50c2-9 8-14 17-14"/><path d="M40 36c9 0 15 5 17 14"/><path d="M16 58c2-10 8-16 16-16s14 6 16 16"/></svg>`,
          bolt: `<svg ${filled}><path d="M36 3 12 35h17l-3 26 26-36H35z"/></svg>`,
          house: `<svg ${filled}><path d="M8 30 32 10l24 20v27H39V40H25v17H8z"/></svg>`,
          bus: `<svg ${filled}><path d="M16 8h32c5 0 9 4 9 9v26c0 4-3 8-7 9v5a4 4 0 0 1-8 0v-4H22v4a4 4 0 0 1-8 0v-5c-4-1-7-5-7-9V17c0-5 4-9 9-9zm2 8v13h28V16H18zm2 27a5 5 0 1 0 0-10 5 5 0 0 0 0 10zm24 0a5 5 0 1 0 0-10 5 5 0 0 0 0 10z"/></svg>`,
          clipboard: `<svg ${common}><rect x="16" y="10" width="36" height="46" rx="4"/><path d="M25 10h18l-2-5H27z"/><path d="M25 25h18M25 35h18M25 45h11"/><path d="m12 49 10-10"/><circle cx="15" cy="52" r="8"/></svg>`,
          comment: `<svg ${common}><path d="M14 18h36a8 8 0 0 1 8 8v8a8 8 0 0 1-8 8H32L18 54V42h-4a8 8 0 0 1-8-8v-8a8 8 0 0 1 8-8z"/><circle cx="25" cy="30" r="2"/><circle cx="34" cy="30" r="2"/><circle cx="43" cy="30" r="2"/></svg>`,
          puzzle: `<svg ${filled}><path d="M24 8h16v12c0 3 3 4 5 2 5-4 11 0 11 6s-6 10-11 6c-2-2-5-1-5 2v20H24V44c0-3-3-4-5-2-5 4-11 0-11-6s6-10 11-6c2 2 5 1 5-2z"/></svg>`,
          tree: `<svg ${common}> <path d="M32 58V10" /><path d="M32 24L20 14" /><path d="M32 27L46 15" /><path d="M32 38L18 29" /><path d="M32 37L45 29" /><path d="M32 49L16 42" /><path d="M32 49L48 40" /><path d="M10 58H54" /></svg>`,
          network: `<svg ${common}><circle cx="32" cy="32" r="6"/><circle cx="12" cy="18" r="5"/><circle cx="52" cy="18" r="5"/><circle cx="12" cy="48" r="5"/><circle cx="52" cy="48" r="5"/><path d="M17 21 27 29M47 21 37 29M17 45 27 35M47 45 37 35"/></svg>`,
          shield: `<svg ${filled}><path d="M32 5 12 14v15c0 15 9 25 20 30 11-5 20-15 20-30V14zM30 43 18 31l5-5 7 7 13-15 5 4z"/></svg>`,
          chart: `<svg ${filled}><path d="M12 50h8V32h-8zm16 0h8V20h-8zm16 0h8V10h-8z"/></svg>`,
          profile: `<svg ${common}><path d="M18 7h25l9 9v41H18z"/><path d="M43 7v11h11"/><circle cx="32" cy="30" r="6"/><path d="M22 47c2-7 7-10 10-10s8 3 10 10"/></svg>`,
          comments: `<svg ${common}><path d="M10 18h28a7 7 0 0 1 7 7v8a7 7 0 0 1-7 7H24L12 50V40h-2a7 7 0 0 1-7-7v-8a7 7 0 0 1 7-7z"/><path d="M38 25h16a7 7 0 0 1 7 7v8a7 7 0 0 1-7 7h-3v9L40 47"/></svg>`,
          file: `<svg ${common}><path d="M18 7h25l9 9v41H18z"/><path d="M43 7v11h11"/><path d="M26 30h20M26 40h20M26 50h12"/></svg>`,
          checkclip: `<svg ${common}><rect x="16" y="9" width="36" height="48" rx="4"/><path d="M25 9h18l-2-5H27z"/><path d="m25 32 6 6 13-15M25 48h19"/></svg>`,
          warning: `<svg ${common}><path d="M32 8 59 55H5z"/><path d="M32 24v14M32 47h.1"/></svg>`,
          arrows: `<svg ${common}><path d="M50 18A23 23 0 0 0 13 30"/><path d="M50 8v10H40"/><path d="M14 46a23 23 0 0 0 37-12"/><path d="M14 56V46h10"/></svg>`,
          ranking: `<svg ${filled}><path d="M13 49h10V31H13zm14 0h10V18H27zm14 0h10V25H41z"/><circle cx="32" cy="10" r="6"/></svg>`,
          folder: `<svg ${filled}><path d="M6 18c0-4 3-7 7-7h13l6 7h19c4 0 7 3 7 7v26c0 4-3 7-7 7H13c-4 0-7-3-7-7z"/></svg>`,
          usersgear: `<svg ${common}><circle cx="22" cy="22" r="7"/><circle cx="42" cy="22" r="7"/><path d="M8 50c2-9 9-14 17-14M39 36c8 0 15 5 17 14"/><circle cx="32" cy="45" r="8"/><path d="m28 45 3 3 6-7"/></svg>`,
          book: `<svg ${common}><path d="M32 54c-6-5-13-7-23-7V12c10 0 17 2 23 7zM32 54c6-5 13-7 23-7V12c-10 0-17 2-23 7z"/></svg>`,
          table: `<svg ${common}><rect x="10" y="13" width="44" height="38" rx="2"/><path d="M10 26h44M10 39h44M25 13v38M40 13v38"/></svg>`,
          landmark: `<svg ${common}><path d="M8 25h48L32 10zM14 25v24M26 25v24M38 25v24M50 25v24M9 54h46"/></svg>`,
          folderopen: `<svg ${common}><path d="M6 22h19l5 7h28l-6 23H12z"/><path d="M10 22v-8h18l5 8"/></svg>`,
          bulb: `<svg ${common}><path d="M42 28c0 7-5 10-7 15h-6c-2-5-7-8-7-15a10 10 0 0 1 20 0z"/><path d="M28 50h8M29 57h6M32 4v6M15 11l4 5M49 11l-4 5"/></svg>`,
          target: `<svg ${common}><circle cx="32" cy="32" r="23"/><circle cx="32" cy="32" r="13"/><circle cx="32" cy="32" r="4"/><path d="M46 18 58 6M49 6h9v9"/></svg>`,
        };
        return icons[name] || icons.file;
      }
      function renderStaticIcons() {
        document.querySelectorAll("[data-icon]").forEach((el) => {
          el.innerHTML = svgIcon(el.dataset.icon);
        });
      }

      const steps = [
        {
          n: 1,
          c: "blue",
          i: "clipboard",
          t: "Preparation",
          d: "Collect evidence and review policies, research, data and existing findings for each sector.",
          o: "Initial Hazard Catalogue",
          oi: "file",
          x: [
            "Review EU/national policy context",
            "Prepare sector evidence packs",
          ],
        },
        {
          n: 2,
          c: "green",
          i: "people",
          t: "Stakeholder Mapping",
          d: "Invite diverse stakeholders including policymakers, experts, NGOs, industry and representatives of vulnerable groups.",
          o: "Diverse & inclusive participant group",
          oi: "people",
          x: [
            "Aim for balanced sector and social representation",
            "Include intermediaries close to disadvantaged groups",
          ],
        },
        {
          n: 3,
          c: "blue",
          i: "comment",
          t: "Persona-Based Exploration",
          d: "Use personas to understand who is affected, how policies impact them and why certain groups bear greater burdens.",
          o: "List of perceived negative impacts",
          oi: "checkclip",
          x: [
            "Start with “Who is affected?”",
            "Capture differentiated and intersectional impacts",
          ],
        },
        {
          n: 4,
          c: "orange",
          i: "puzzle",
          t: "Cluster Impacts into Hazards",
          d: "Group similar impacts and define clear, concise hazard statements.",
          o: "Draft Hazard List",
          oi: "warning",
          x: ["Merge duplicates", "Use short observable hazard wording"],
        },
        {
          n: 5,
          c: "teal",
          i: "tree",
          t: "Root Cause Analysis",
          d: "Ask “Why?” repeatedly to identify underlying causes and classify them across five system dimensions.",
          o: "Root causes by dimension",
          oi: "puzzle",
          x: [
            "Environment, society, economy, governance, technology",
            "Identify structural causes, not only symptoms",
          ],
        },
        {
          n: 6,
          c: "blue",
          i: "network",
          t: "System Mapping",
          d: "Map the relationships: drivers, policies, hazards, affected groups, impacts and feedback loops.",
          o: "Sectoral system map",
          oi: "arrows",
          x: [
            "Show causal links",
            "Identify barriers, drivers and feedback loops",
          ],
        },
        {
          n: 7,
          c: "green",
          i: "shield",
          t: "Hazard Validation",
          d: "Check each hazard for relevance, evidence, observability, actionability and clarity. Remove weak or duplicate items.",
          o: "Validated Hazard List",
          oi: "shield",
          x: [
            "Linked to transition policy",
            "Supported by evidence and understandable",
          ],
        },
        {
          n: 8,
          c: "orange",
          i: "chart",
          t: "Hazard Prioritization",
          d: "Score hazards by severity, likelihood, population affected, urgency and policy relevance. Prioritize the highest.",
          o: "Hazard Prioritization Matrix",
          oi: "ranking",
          x: [
            "Use transparent scoring",
            "Prioritize hazards affecting vulnerable groups",
          ],
        },
        {
          n: 9,
          c: "purple",
          i: "profile",
          t: "Define Hazard Profiles",
          d: "Document key information for each priority hazard and link it to policies, causes, evidence and affected groups.",
          o: "Hazard Profiles",
          oi: "folder",
          x: [
            "Hazard title, description and sector",
            "Causes, drivers, barriers and evidence",
          ],
        },
        {
          n: 10,
          c: "teal",
          i: "comments",
          t: "Validation Workshop",
          d: "Present the hazard list to a broader audience. Participants confirm, modify, merge, remove or add hazards.",
          o: "Final validated hazard catalogue",
          oi: "usersgear",
          x: [
            "Broader review by experts and policymakers",
            "Final agreement before mitigation design",
          ],
        },
      ];
      const root = document.getElementById("steps");
      renderStaticIcons();
      steps.forEach((s, idx) => {
        root.insertAdjacentHTML(
          "beforeend",
          `<article class="step ${s.c}" data-step-toggle="true" tabindex="0"><div class="num ${s.c}">${s.n}</div><div class="iconwrap">${svgIcon(s.i)}</div><div class="txt"><h3>${s.t}</h3><p>${s.d}</p><div class="details"><ul>${s.x.map((v) => `<li>${v}</li>`).join("")}</ul></div></div><div class="arrow">▶</div><div class="output">${svgIcon(s.oi)}<span><b>Output:</b>${s.o}</span></div></article>${idx < steps.length - 1 ? '<div class="down">↓</div>' : ""}`,
        );
      });
      function expandAll() {
        document
          .querySelectorAll(".step")
          .forEach((e) => e.classList.add("active"));
      }
      function collapseAll() {
        document
          .querySelectorAll(".step")
          .forEach((e) => e.classList.remove("active"));
      }
      document.getElementById("expandAllButton")?.addEventListener("click", expandAll);
      document.getElementById("collapseAllButton")?.addEventListener("click", collapseAll);
      root.addEventListener("click", (event) => {
        const step = event.target.closest("[data-step-toggle]");
        if (step) step.classList.toggle("active");
      });
      root.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const step = event.target.closest("[data-step-toggle]");
        if (!step) return;
        event.preventDefault();
        step.classList.toggle("active");
      });

