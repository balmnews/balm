<?xml version="1.0" encoding="UTF-8"?>
<!--
  Renders docs/feed.xml as a readable page when a browser opens the raw feed
  URL directly (e.g. someone clicking "subscribe by RSS" without a feed
  reader). Feed readers ignore this stylesheet entirely and parse the
  underlying RSS/XML exactly as before — this only changes what a browser
  shows when it hits the file directly.
-->
<xsl:stylesheet version="1.0"
    xmlns:xsl="http://www.w3.org/1999/XSL/Transform"
    xmlns:atom="http://www.w3.org/2005/Atom">
<xsl:output method="html" encoding="UTF-8" indent="yes"/>

<xsl:template match="/rss/channel">
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title><xsl:value-of select="title"/> — RSS Feed</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin="anonymous"/>
  <link href="https://fonts.googleapis.com/css2?family=Caveat:wght@700&amp;family=Playfair+Display:ital,wght@0,400;0,600;1,400&amp;family=Source+Serif+4:ital,wght@0,300;1,300&amp;display=swap" rel="stylesheet"/>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #f2ede4;
      --ink: #2a2520;
      --secondary: #6a6058;
      --muted: #9a9288;
      --rule: #c8c0b4;
      --accent: #6b82a8;
    }
    html { font-size: 18px; }
    body {
      background: var(--bg);
      color: var(--ink);
      font-family: 'Source Serif 4', serif;
      font-weight: 300;
      line-height: 1.6;
      padding: 3rem 1.5rem 4rem;
    }
    .wrap { max-width: 640px; margin: 0 auto; }
    .masthead { font-family: 'Caveat', cursive; font-weight: 700; font-size: 3.2rem; color: var(--accent); }
    .tagline { font-style: italic; color: var(--secondary); margin-top: 0.25rem; }
    .notice {
      margin-top: 2rem;
      padding: 1rem 1.25rem;
      background: #ede9e2;
      border-left: 3px solid var(--accent);
      font-size: 0.95rem;
      color: var(--secondary);
    }
    .notice p + p { margin-top: 0.7rem; }
    .notice a { color: var(--accent); }
    .notice code {
      display: inline-block;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.85rem;
      color: var(--ink);
      background: rgba(0,0,0,0.05);
      padding: 0.15rem 0.4rem;
      border-radius: 3px;
      word-break: break-all;
    }
    h2 {
      font-family: 'Playfair Display', serif;
      font-weight: 400;
      margin-top: 2.5rem;
      padding-bottom: 0.5rem;
      border-bottom: 1px solid var(--rule);
    }
    ul { list-style: none; margin-top: 1rem; }
    li { padding: 0.9rem 0; border-bottom: 1px solid var(--rule); }
    li a {
      font-family: 'Playfair Display', serif;
      color: var(--ink);
      text-decoration: none;
      font-size: 1.15rem;
    }
    li a:hover { color: var(--accent); }
    .pubdate { display: block; color: var(--muted); font-size: 0.85rem; margin-top: 0.2rem; }
    footer { margin-top: 3rem; font-size: 0.9rem; color: var(--muted); }
    footer a { color: var(--accent); }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="masthead">Balm</div>
    <p class="tagline"><xsl:value-of select="description"/></p>

    <div class="notice">
      <p>This is Balm's RSS feed — a plain list of editions that feed reader
      apps (Feedly, NetNewsWire, Reeder, and others) use to check for new
      issues automatically. There is nothing to do on this page.</p>
      <p class="feed-url">To subscribe, paste this address into a feed reader:
      <code><xsl:value-of select="atom:link[@rel='self']/@href"/></code></p>
      <p class="feed-url">If you don't use a feed reader, you don't need one —
      just visit <a href="/index.html">balm.news</a> whenever you like.</p>
    </div>

    <h2>Recent editions</h2>
    <ul>
      <xsl:for-each select="item">
        <li>
          <a href="{link}"><xsl:value-of select="title"/></a>
          <span class="pubdate"><xsl:value-of select="pubDate"/></span>
        </li>
      </xsl:for-each>
    </ul>

    <footer>
      <a href="/index.html">← Back to Balm</a>
    </footer>
  </div>
</body>
</html>
</xsl:template>
</xsl:stylesheet>
