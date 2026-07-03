import unittest

from app.services.document_text import html_to_text, remove_web_boilerplate_text


class DocumentTextTests(unittest.TestCase):
    def test_html_to_text_skips_ads_and_boilerplate_containers(self) -> None:
        html = """
        <html>
          <body>
            <header>Subscribe now</header>
            <nav>Home About Contact</nav>
            <main>
              <article>
                <h1>Energy retrofit costs are rising</h1>
                <p>Low-income households face higher upfront renovation costs.</p>
                <div class="ad banner">Advertisement Buy this product</div>
                <aside>Related articles about housing</aside>
                <p>Targeted grants can reduce affordability pressure.</p>
              </article>
            </main>
            <footer>Cookie settings</footer>
          </body>
        </html>
        """

        text = html_to_text(html)

        self.assertIn("Energy retrofit costs are rising", text)
        self.assertIn("Low-income households face higher upfront renovation costs.", text)
        self.assertIn("Targeted grants can reduce affordability pressure.", text)
        self.assertNotIn("Advertisement", text)
        self.assertNotIn("Subscribe now", text)
        self.assertNotIn("Cookie settings", text)
        self.assertNotIn("Related articles", text)

    def test_remove_web_boilerplate_text_keeps_substantive_lines(self) -> None:
        text = "\n".join(
            (
                "Advertisement",
                "Households face energy poverty risk.",
                "Subscribe to our newsletter",
            )
        )

        self.assertEqual(
            remove_web_boilerplate_text(text),
            "Households face energy poverty risk.",
        )


if __name__ == "__main__":
    unittest.main()
