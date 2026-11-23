"""Generate the FITTER-EU read-only API workshop presentation."""

from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Inches, Pt


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
OUT = DOCS / "FITTER_EU_API_WORKSHOP.pptx"

SW, SH = 13.333, 7.5
NAVY = "172033"
INK = "273449"
MUTED = "63738A"
PAPER = "F7F8FB"
WHITE = "FFFFFF"
TEAL = "0B8A83"
TEAL_PALE = "DDF4F0"
VIOLET = "6D35D4"
VIOLET_PALE = "F0E9FF"
GOLD = "EAB64A"
GOLD_PALE = "FFF4D4"
LINE = "D8DFE9"
BLUE = "35658D"
RED_PALE = "FDE8E7"


def rgb(hex_value):
    return RGBColor.from_string(hex_value)


def add_shape(slide, kind, x, y, w, h, fill, line=None, radius=False):
    shape = slide.shapes.add_shape(kind, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = rgb(fill)
    shape.line.color.rgb = rgb(line or fill)
    shape.line.width = Pt(0.8)
    return shape


def text(slide, value, x, y, w, h, size=16, color=INK, bold=False,
         font="Aptos", align=PP_ALIGN.LEFT, valign=MSO_ANCHOR.TOP,
         margin=0.02, italic=False):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = Inches(margin)
    tf.margin_right = Inches(margin)
    tf.margin_top = Inches(margin)
    tf.margin_bottom = Inches(margin)
    tf.vertical_anchor = valign
    for i, line in enumerate(value.split("\n")):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(3)
        run = p.add_run()
        run.text = line
        run.font.name = font
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.italic = italic
        run.font.color.rgb = rgb(color)
    return box


def richline(slide, parts, x, y, w, h, size=16, color=INK):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = Inches(0.02)
    tf.margin_top = tf.margin_bottom = Inches(0.02)
    p = tf.paragraphs[0]
    for content, is_bold, run_color in parts:
        run = p.add_run()
        run.text = content
        run.font.name = "Aptos"
        run.font.size = Pt(size)
        run.font.bold = is_bold
        run.font.color.rgb = rgb(run_color or color)
    return box


def card(slide, x, y, w, h, title, body, accent=TEAL, title_size=18, body_size=13):
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, WHITE, LINE)
    add_shape(slide, MSO_SHAPE.RECTANGLE, x, y + 0.04, 0.07, h - 0.08, accent)
    text(slide, title, x + 0.22, y + 0.18, w - 0.4, 0.36, title_size, NAVY, True)
    text(slide, body, x + 0.22, y + 0.62, w - 0.42, h - 0.75, body_size, MUTED)


def pill(slide, label, x, y, w, fill=TEAL_PALE, color=TEAL):
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, 0.33, fill)
    text(slide, label, x + 0.06, y + 0.015, w - 0.12, 0.29, 10, color, True,
         align=PP_ALIGN.CENTER, valign=MSO_ANCHOR.MIDDLE)


def base_slide(prs, title, kicker=None, dark=False):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = NAVY if dark else PAPER
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = rgb(bg)
    if kicker:
        text(slide, kicker.upper(), 0.62, 0.34, 11.9, 0.25, 10,
             GOLD if dark else TEAL, True)
    text(slide, title, 0.62, 0.72, 12.1, 0.65, 28,
         WHITE if dark else NAVY, True)
    # Footer and page number.
    text(slide, "FITTER-EU  •  READ-ONLY API WORKSHOP", 0.65, 7.13, 8.0, 0.18,
         8, "BCC7D5" if dark else MUTED, True)
    text(slide, f"{len(prs.slides):02d}", 12.25, 7.1, 0.45, 0.2,
         9, "BCC7D5" if dark else MUTED, True, align=PP_ALIGN.RIGHT)
    return slide


def codebox(slide, code, x, y, w, h, size=12):
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, NAVY)
    text(slide, code, x + 0.2, y + 0.14, w - 0.4, h - 0.25,
         size, "E7EDF5", font="Cascadia Code")


def node(slide, title, sub, x, y, w, h, fill=WHITE, accent=TEAL, title_size=15):
    add_shape(slide, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, w, h, fill, LINE)
    add_shape(slide, MSO_SHAPE.OVAL, x + 0.18, y + 0.16, 0.34, 0.34, accent)
    text(slide, title, x + 0.64, y + 0.12, w - 0.78, 0.34, title_size, NAVY, True)
    text(slide, sub, x + 0.22, y + 0.61, w - 0.44, h - 0.7, 11, MUTED)


def arrow(slide, x1, y1, x2, y2, color=TEAL, width=2.2):
    conn = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,
                                      Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = rgb(color)
    conn.line.width = Pt(width)
    conn.line.end_arrowhead = True
    return conn


def add_flow_image(slide, filename, x, y, w, h):
    path = DOCS / filename
    slide.shapes.add_picture(str(path), Inches(x), Inches(y), width=Inches(w), height=Inches(h))


def build():
    prs = Presentation()
    prs.slide_width = Inches(SW)
    prs.slide_height = Inches(SH)
    prs.core_properties.title = "FITTER-EU Read-Only API Workshop"
    prs.core_properties.subject = "Hands-on workshop for the FITTER-EU API cookbook"
    prs.core_properties.author = "Fitter FairImpact Platform"

    # 1 — Cover
    s = prs.slides.add_slide(prs.slide_layouts[6])
    s.background.fill.solid(); s.background.fill.fore_color.rgb = rgb(NAVY)
    add_shape(s, MSO_SHAPE.RECTANGLE, 9.65, 0, 3.68, 7.5, "202C43")
    add_shape(s, MSO_SHAPE.OVAL, 10.5, 1.25, 1.25, 1.25, TEAL)
    add_shape(s, MSO_SHAPE.OVAL, 11.55, 3.05, 0.85, 0.85, VIOLET)
    add_shape(s, MSO_SHAPE.OVAL, 10.2, 4.7, 1.55, 1.55, GOLD)
    arrow(s, 10.9, 2.5, 11.7, 3.05, "A9B5C8", 2)
    arrow(s, 11.8, 3.95, 11.15, 4.8, "A9B5C8", 2)
    pill(s, "HANDS-ON WORKSHOP", 0.8, 0.82, 2.1, fill="29364D", color=GOLD)
    text(s, "From policy\nto impact", 0.8, 1.55, 8.4, 1.85, 38, WHITE, True)
    text(s, "FITTER-EU read-only API", 0.85, 3.76, 7.8, 0.48, 23, "D5DEEB", True)
    text(s, "A practical guide to context, policy-linked hazards,\naffected groups, mitigation, and saved analyses.",
         0.85, 4.43, 7.9, 0.86, 17, "BCC7D5")
    text(s, "Based on the FITTER-EU API cookbook", 0.85, 6.65, 7.4, 0.28, 11, "BCC7D5")
    text(s, "01", 12.25, 7.1, 0.45, 0.2, 9, "BCC7D5", True, align=PP_ALIGN.RIGHT)

    # 2 — Objectives
    s = base_slide(prs, "What you’ll be able to do", "Workshop outcomes")
    cards = [
        ("Establish context", "Resolve country, region, and sector IDs before making dependent calls.", TEAL),
        ("Trace a policy", "Find policies, then request hazards linked to the chosen policy.", VIOLET),
        ("Explore impacts", "Retrieve population profiles and mitigation measures for a hazard.", GOLD),
        ("Review a session", "List your saved sessions and export data or a scoped PDF report.", BLUE),
    ]
    for i, (t, b, a) in enumerate(cards):
        x = 0.7 + (i % 2) * 6.05
        y = 1.75 + (i // 2) * 2.15
        card(s, x, y, 5.75, 1.72, t, b, a)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.7, 6.18, 11.95, 0.56, GOLD_PALE)
    text(s, "Workshop rule: IDs connect calls; the API returns data for inspection and reporting.",
         0.95, 6.3, 11.4, 0.28, 14, NAVY, True, align=PP_ALIGN.CENTER)

    # 3 — Purpose of the cookbook and APIs
    s = base_slide(prs, "Why the cookbook and APIs are being developed", "Purpose")
    card(s, 0.72, 1.72, 5.75, 2.02, "The cookbook: a shared data guide",
         "Defines the data structure, field meanings, relationships, and contribution conventions. This helps contributors prepare information consistently and helps readers interpret it in context.",
         VIOLET, 18, 13)
    card(s, 6.78, 1.72, 5.75, 2.02, "The APIs: a consistent access layer",
         "Provide authenticated, read-only access to reference listings, analysis results, and a user's saved sessions and reports. Compatible tools can retrieve the approved data without querying the database directly.",
         TEAL, 18, 13)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.72, 4.15, 11.8, 1.04, NAVY)
    text(s, "Together", 1.0, 4.36, 1.35, 0.35, 17, GOLD, True)
    text(s, "The cookbook describes what the data means; the APIs make that data available through a documented, controlled interface.",
         2.35, 4.34, 9.75, 0.52, 15, WHITE, True, valign=MSO_ANCHOR.MIDDLE)
    text(s, "Service boundary", 0.92, 5.65, 1.75, 0.3, 12, MUTED, True)
    text(s, "The documented API is read-only: it retrieves records, analysis, and reports. It does not change reference data or advance the guided conversation.",
         2.75, 5.6, 9.4, 0.54, 13, INK)
    text(s, "The cookbook version also explains endpoint structure and the scope of each API service. Public base URL and final authentication details are planned for release toward project end.",
         0.92, 6.42, 11.4, 0.35, 11, MUTED, italic=True, align=PP_ALIGN.CENTER)

    # 4 — Platform overview
    s = base_slide(prs, "Platform overview · From context to a shareable analysis", "How Fitter FairImpact Platform uses the API")
    overview = [
        ("1", "Choose context", "Country · optional region · sector", TEAL),
        ("2", "Review policy", "Policies available for the selected context", VIOLET),
        ("3", "Explore hazards", "Policy-linked hazards and separate context ranking", GOLD),
        ("4", "Assess impacts", "Affected groups and mitigation options", BLUE),
        ("5", "Save & communicate", "Owned session · export · scoped PDF report", TEAL),
    ]
    for i, (n, title, desc, accent) in enumerate(overview):
        x = 0.58 + i * 2.58
        add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 2.06, 2.27, 2.08, WHITE, LINE)
        add_shape(s, MSO_SHAPE.OVAL, x + 0.16, 2.24, 0.42, 0.42, accent)
        text(s, n, x + 0.16, 2.29, 0.42, 0.28, 12, WHITE, True, align=PP_ALIGN.CENTER)
        text(s, title, x + 0.16, 2.92, 1.95, 0.55, 15, NAVY, True, align=PP_ALIGN.CENTER)
        text(s, desc, x + 0.17, 3.48, 1.93, 0.48, 10, MUTED, align=PP_ALIGN.CENTER)
        if i < len(overview) - 1:
            arrow(s, x + 2.28, 3.08, x + 2.55, 3.08, TEAL, 1.8)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.82, 4.75, 11.55, 0.8, TEAL_PALE)
    text(s, "The API supports this journey with listings, analysis endpoints, and owner-scoped session/report endpoints.",
         1.12, 4.96, 10.95, 0.36, 14, NAVY, True, align=PP_ALIGN.CENTER)
    text(s, "The end-to-end guided experience lives in the platform. The API provides read-only access to data and outputs for inspection and compatible tools.",
         1.0, 5.9, 11.2, 0.6, 13, MUTED, align=PP_ALIGN.CENTER)

    # 5 — Workflow diagram
    s = base_slide(prs, "The API follows the analysis journey", "01 · Map the workflow")
    node(s, "Context", "countries · regions · sectors", 0.65, 2.0, 2.4, 1.5, accent=TEAL)
    node(s, "Policies", "country_id + sector_id", 3.6, 2.0, 2.4, 1.5, accent=VIOLET)
    node(s, "Linked hazards", "policy_id + context filters", 6.55, 2.0, 2.55, 1.5, accent=GOLD)
    node(s, "People & measures", "hazard_id → profiles / measures", 9.65, 2.0, 2.95, 1.5, accent=BLUE)
    arrow(s, 3.05, 2.75, 3.58, 2.75)
    arrow(s, 6.0, 2.75, 6.53, 2.75)
    arrow(s, 9.12, 2.75, 9.63, 2.75)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 2.52, 4.42, 3.7, 1.1, VIOLET_PALE)
    text(s, "Separate analysis branch", 2.78, 4.58, 3.2, 0.27, 14, VIOLET, True)
    text(s, "GET /api/hazards/ranked\nContext-wide; no policy_id", 2.78, 4.9, 3.2, 0.48, 11, INK)
    arrow(s, 4.82, 3.52, 4.4, 4.4, VIOLET, 1.8)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 7.15, 4.42, 4.25, 1.1, TEAL_PALE)
    text(s, "Saved analysis branch", 7.42, 4.58, 3.7, 0.27, 14, TEAL, True)
    text(s, "sessions → export / PDF report\nOwner-scoped to signed-in user", 7.42, 4.9, 3.7, 0.48, 11, INK)
    text(s, "Common call order, not a mandatory sequence when you already have the IDs.",
         1.0, 6.35, 11.4, 0.28, 12, MUTED, italic=True, align=PP_ALIGN.CENTER)

    # 4 — API surface
    s = base_slide(prs, "A compact read-only API surface", "02 · Know the endpoint groups")
    groups = [
        ("Context & catalogues", "countries · regions · sectors", "3 endpoints", TEAL),
        ("Policies & hazards", "policies · policy-linked hazards", "2 endpoints", VIOLET),
        ("Hazard analysis", "ranked · salience · effect size", "3 endpoints", GOLD),
        ("Impact & response", "socio-demographics · mitigation", "2 endpoints", BLUE),
        ("Saved analyses", "sessions · export · PDF report", "3 endpoints", TEAL),
        ("Admin knowledge", "knowledge policy choices", "1 endpoint", VIOLET),
    ]
    for i, (t, b, count, a) in enumerate(groups):
        x = 0.7 + (i % 3) * 4.1
        y = 1.72 + (i // 3) * 1.82
        card(s, x, y, 3.75, 1.45, t, b, a, title_size=16, body_size=12)
        pill(s, count, x + 2.45, y + 1.05, 1.1, fill=TEAL_PALE if a == TEAL else VIOLET_PALE,
             color=a)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.7, 5.75, 11.95, 0.77, NAVY)
    text(s, "14 documented GET endpoints", 1.0, 5.93, 4.4, 0.32, 17, WHITE, True)
    text(s, "+ POST /login in Swagger to establish the existing session", 5.1, 5.97, 7.0, 0.27,
         13, "D5DEEB")
    text(s, "The public base URL and final authentication mechanism are planned for release toward project end.",
         0.95, 6.68, 11.5, 0.25, 11, MUTED, italic=True)

    # 5 — Login
    s = base_slide(prs, "Authorize in Swagger", "03 · Establish your session")
    steps = [
        ("1", "Open Swagger", "Go to /docs on the deployment you are using."),
        ("2", "Run POST /login", "Enter account email and password as form fields."),
        ("3", "Use the GET routes", "The browser sends the session cookie to same-origin requests."),
    ]
    for i, (n, t, b) in enumerate(steps):
        x = 0.72 + i * 4.1
        add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, 1.68, 3.72, 1.55, WHITE, LINE)
        add_shape(s, MSO_SHAPE.OVAL, x + 0.2, 1.9, 0.52, 0.52, TEAL if i < 2 else VIOLET)
        text(s, n, x + 0.2, 1.96, 0.52, 0.35, 14, WHITE, True, align=PP_ALIGN.CENTER)
        text(s, t, x + 0.86, 1.85, 2.6, 0.32, 16, NAVY, True)
        text(s, b, x + 0.86, 2.28, 2.58, 0.65, 11, MUTED)
    codebox(s, "POST /login\nContent-Type: application/x-www-form-urlencoded\n\nemail=<account-email>&password=<account-password>",
            0.75, 3.72, 6.1, 1.58, 12)
    card(s, 7.2, 3.72, 5.2, 1.58, "What happens", "Login establishes the app session. Swagger reuses its cookie for the protected GET calls.", TEAL)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.75, 5.75, 11.65, 0.7, GOLD_PALE)
    text(s, "Keep credentials private. Use the final project instructions for the public host and authentication when released.",
         1.0, 5.94, 11.15, 0.3, 13, NAVY, True, align=PP_ALIGN.CENTER)

    # 6 — Example 1
    s = base_slide(prs, "Example 1 · Select context, then list policies", "04 · Follow the cookbook examples")
    add_flow_image(s, "FITTER_EU_API_EXAMPLE_1.png", 0.75, 1.62, 11.85, 2.38)
    codebox(s, "GET /api/v1/listings/countries\nGET /api/v1/listings/sectors?country_id=<country-id>\nGET /api/v1/listings/policies?country_id=<country-id>&sector_id=<sector-id>",
            0.82, 4.42, 7.15, 1.5, 11)
    card(s, 8.35, 4.42, 4.05, 1.5, "Result", "Keep the returned IDs. Policies are returned in policies and original_policies arrays.", VIOLET, 16, 12)
    text(s, "Optional: call /regions?country_id=… when a subnational context is needed.",
         0.95, 6.35, 11.4, 0.28, 12, MUTED, italic=True, align=PP_ALIGN.CENTER)

    # 7 — Example 2
    s = base_slide(prs, "Example 2 · Ask for the selected policy’s hazards", "05 · Keep policy lookup distinct")
    add_flow_image(s, "FITTER_EU_API_EXAMPLE_2.png", 0.75, 1.62, 11.85, 2.38)
    codebox(s, "GET /api/v1/listings/hazards?policy_id=<policy-id>&country_id=<country-id>&sector_id=<sector-id>\n\nOptional: &region_id=<region-id>",
            0.82, 4.42, 7.15, 1.5, 11)
    card(s, 8.35, 4.42, 4.05, 1.5, "Policy-specific", "The hazards listing accepts policy_id and returns hazards linked to that policy.", VIOLET, 16, 12)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.82, 6.13, 11.58, 0.57, VIOLET_PALE)
    text(s, "GET /api/hazards/ranked is separate: a context-wide ranking, without policy_id.",
         1.05, 6.28, 11.1, 0.28, 13, VIOLET, True, align=PP_ALIGN.CENTER)

    # 8 — Example 3
    s = base_slide(prs, "Example 3 · Explore affected population groups", "06 · Connect a hazard to profiles")
    add_flow_image(s, "FITTER_EU_API_EXAMPLE_3.png", 0.75, 1.62, 11.85, 2.38)
    codebox(s, "GET /api/v1/listings/socio-demographics?country_id=<country-id>\\\n  &sector_id=<sector-id>&hazard_id=<hazard-id>",
            0.82, 4.42, 7.15, 1.28, 11)
    card(s, 8.35, 4.42, 4.05, 1.5, "Review the response", "Use profile labels and descriptions to interpret which groups are represented.", BLUE, 16, 12)
    text(s, "For session-specific profiles, include an owned session_key; the route enforces ownership.",
         0.95, 6.25, 11.4, 0.3, 12, MUTED, italic=True, align=PP_ALIGN.CENTER)

    # 9 — Example 4
    s = base_slide(prs, "Example 4 · Compare mitigation measures", "07 · Check who each measure targets")
    add_flow_image(s, "FITTER_EU_API_EXAMPLE_4.png", 0.75, 1.62, 11.85, 2.38)
    codebox(s, "GET /api/v1/listings/mitigation-measures?country_id=<country-id>\\\n  &sector_id=<sector-id>&hazard_id=<hazard-id>",
            0.82, 4.42, 7.15, 1.28, 11)
    card(s, 8.35, 4.42, 4.05, 1.5, "Compare", "Read the measure, hazard links, target groups, and targeting information together.", TEAL, 16, 12)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.82, 6.13, 11.58, 0.57, GOLD_PALE)
    text(s, "A recorded target-group link supports review; it does not prove that a measure is effective.",
         1.05, 6.28, 11.1, 0.28, 13, NAVY, True, align=PP_ALIGN.CENTER)

    # 10 — Sessions
    s = base_slide(prs, "Example 5 · Revisit or report on a saved analysis", "08 · Work with owned sessions")
    add_flow_image(s, "FITTER_EU_API_EXAMPLE_5.png", 0.75, 1.62, 11.85, 2.38)
    codebox(s, "GET /api/sessions\nGET /api/sessions/<session-key>/export\nGET /api/sessions/<session-key>/report?scope=current",
            0.82, 4.42, 7.15, 1.5, 11)
    card(s, 8.35, 4.42, 4.05, 1.5, "Owner-scoped", "Exports and reports are available only for sessions owned by the signed-in user.", VIOLET, 16, 12)
    text(s, "Report scope: current (default) · user_hazard · all_hazard", 0.95, 6.3, 11.4, 0.3,
         13, MUTED, True, align=PP_ALIGN.CENTER)

    # 11 — Exercise
    s = base_slide(prs, "Small-group exercise · Trace one policy to response options", "09 · Try it together")
    exercise = [
        ("A", "Choose a context", "Find a country and sector. Record their IDs."),
        ("B", "Select a policy", "List policies for the context and choose one."),
        ("C", "Follow the hazard link", "Call hazards with policy_id and the context IDs."),
        ("D", "Explore response", "Use hazard_id for profiles and mitigation measures."),
    ]
    for i, (letter, title, body) in enumerate(exercise):
        x = 0.72 + (i % 2) * 6.05
        y = 1.75 + (i // 2) * 1.8
        add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x, y, 5.7, 1.42, WHITE, LINE)
        add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, x + 0.2, y + 0.22, 0.52, 0.52,
                  [TEAL, VIOLET, GOLD, BLUE][i])
        text(s, letter, x + 0.2, y + 0.29, 0.52, 0.3, 15, WHITE, True, align=PP_ALIGN.CENTER)
        text(s, title, x + 0.9, y + 0.2, 4.4, 0.35, 16, NAVY, True)
        text(s, body, x + 0.9, y + 0.66, 4.4, 0.52, 12, MUTED)
    add_shape(s, MSO_SHAPE.ROUNDED_RECTANGLE, 0.72, 5.75, 11.75, 0.78, NAVY)
    text(s, "Discuss: Which result is policy-specific? Which is context-wide? What would you inspect next?",
         1.0, 5.98, 11.2, 0.34, 14, WHITE, True, align=PP_ALIGN.CENTER)

    # 12 — Takeaways
    s = base_slide(prs, "A few habits make the API easier to use", "10 · Takeaways", dark=True)
    takeaways = [
        ("Carry IDs forward", "Use returned IDs as filters in related listings."),
        ("Read the endpoint scope", "Policy-linked hazards and context-wide ranking answer different questions."),
        ("Page through listings", "Default page size is 100; maximum is 500."),
        ("Respect ownership", "Session exports, reports, and session profiles are owner-scoped."),
        ("Use the cookbook as a map", "It explains API structure and service scope; public connection details come later."),
    ]
    for i, (head, body) in enumerate(takeaways):
        y = 1.62 + i * 0.88
        add_shape(s, MSO_SHAPE.OVAL, 0.92, y + 0.04, 0.28, 0.28, [TEAL, VIOLET, GOLD, BLUE, TEAL][i])
        text(s, head, 1.42, y, 3.15, 0.3, 15, WHITE, True)
        text(s, body, 4.7, y, 7.3, 0.42, 13, "D5DEEB")
    pill(s, "QUESTIONS & DISCUSSION", 0.92, 6.26, 2.7, fill="29364D", color=GOLD)

    prs.save(OUT)
    print(f"Created {OUT}")
    print(f"Slides: {len(prs.slides)}")


if __name__ == "__main__":
    build()
