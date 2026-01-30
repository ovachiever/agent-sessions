"""Pattern-based tag generation for session indexing."""

import re
from typing import Optional
from agent_sessions.models import Session
from agent_sessions.search import extract_text_content


class AutoTagger:
    """Generate tags from session content using pattern matching."""

    # Tool patterns: (regex, tag_prefix)
    TOOL_PATTERNS = [
        (r"agent-do\s+(\w+)", "tool:agent-do-{0}"),
        (r"\bgit\s+(commit|push|pull|rebase|merge|branch|checkout)", "tool:git"),
        (r"\bnpm\s+(install|run|test|build|start)", "tool:npm"),
        (r"\bdocker\s+(build|run|compose|push|pull)", "tool:docker"),
        (r"\bpytest\b", "tool:pytest"),
        (r"\bpython\s+-m\s+pytest", "tool:pytest"),
        (r"\brg\b|\bripgrep\b", "tool:ripgrep"),
        (r"\blsp_\w+", "tool:lsp"),
        (r"\bast_grep", "tool:ast-grep"),
        (r"\bgrep\b", "tool:grep"),
        (r"\bfind\b", "tool:find"),
        (r"\bls\b", "tool:ls"),
        (r"\bcat\b", "tool:cat"),
        (r"\bsed\b", "tool:sed"),
        (r"\bawk\b", "tool:awk"),
        (r"\bjq\b", "tool:jq"),
        (r"\bcurl\b", "tool:curl"),
        (r"\bwget\b", "tool:wget"),
        (r"\bvim\b|\bvi\b", "tool:vim"),
        (r"\btmux\b", "tool:tmux"),
        (r"\bvscode\b|\bcode\b", "tool:vscode"),
    ]

    # Activity patterns
    ACTIVITY_PATTERNS = [
        (r"\b(fix|debug|troubleshoot|diagnose|trace|profile)\b", "debugging"),
        (r"\b(implement|add|create|build|write|develop)\b", "implementing"),
        (r"\b(refactor|restructure|reorganize|rewrite|clean|simplify)\b", "refactoring"),
        (r"\b(test|spec|coverage|assert|validate|verify)\b", "testing"),
        (r"\b(document|comment|explain|describe|annotate)\b", "documenting"),
        (r"\b(review|audit|analyze|inspect|examine)\b", "reviewing"),
        (r"\b(optimize|improve|enhance|speed|performance)\b", "optimizing"),
        (r"\b(deploy|release|publish|ship|launch)\b", "deploying"),
        (r"\b(migrate|upgrade|update|patch|version)\b", "migrating"),
        (r"\b(integrate|connect|link|bind|wire)\b", "integrating"),
    ]

    # Technology patterns
    TECH_PATTERNS = [
        # Frontend frameworks
        (r"\breact\b", "react"),
        (r"\bvue\b", "vue"),
        (r"\bangular\b", "angular"),
        (r"\bsvelte\b", "svelte"),
        (r"\bnext\.?js\b", "nextjs"),
        (r"\bnuxt\b", "nuxt"),
        (r"\bast?ro\b", "astro"),
        # Languages
        (r"\bpython\b", "python"),
        (r"\bjavascript\b|\bjs\b", "javascript"),
        (r"\btypescript\b|\bts\b", "typescript"),
        (r"\bruby\b", "ruby"),
        (r"\bjava\b", "java"),
        (r"\bgo\b|\bgolang\b", "go"),
        (r"\brust\b", "rust"),
        (r"\bc\+\+\b|\bcpp\b", "cpp"),
        (r"\bc#\b|\bcsharp\b", "csharp"),
        (r"\bphp\b", "php"),
        # Databases
        (r"\bpostgres\b|\bpostgresql\b", "postgres"),
        (r"\bmysql\b", "mysql"),
        (r"\bsqlite\b", "sqlite"),
        (r"\bmongodb\b|\bmongo\b", "mongodb"),
        (r"\bredis\b", "redis"),
        (r"\bfirebase\b", "firebase"),
        (r"\bdynamodb\b", "dynamodb"),
        # ORMs/Query builders
        (r"\bprisma\b", "prisma"),
        (r"\bdrizzle\b", "drizzle"),
        (r"\btypeorm\b", "typeorm"),
        (r"\bsqlalchemy\b", "sqlalchemy"),
        (r"\bsequelize\b", "sequelize"),
        # Testing
        (r"\bjest\b", "jest"),
        (r"\bvitest\b", "vitest"),
        (r"\bmocha\b", "mocha"),
        (r"\brspec\b", "rspec"),
        (r"\bunittest\b", "unittest"),
        # Build/Package tools
        (r"\bwebpack\b", "webpack"),
        (r"\bvite\b", "vite"),
        (r"\besbuild\b", "esbuild"),
        (r"\brollup\b", "rollup"),
        (r"\bpnpm\b", "pnpm"),
        (r"\byarn\b", "yarn"),
        # Cloud/Infrastructure
        (r"\bcloudflare\b", "cloudflare"),
        (r"\baws\b", "aws"),
        (r"\bazure\b", "azure"),
        (r"\bgcp\b|\bgoogle\s+cloud\b", "gcp"),
        (r"\bvercel\b", "vercel"),
        (r"\bnetlify\b", "netlify"),
        (r"\bheroku\b", "heroku"),
        (r"\bdocker\b", "docker"),
        (r"\bkubernetes\b|\bk8s\b", "kubernetes"),
        # APIs/Frameworks
        (r"\bexpress\b", "express"),
        (r"\bfastapi\b", "fastapi"),
        (r"\bdjango\b", "django"),
        (r"\brails\b", "rails"),
        (r"\bflask\b", "flask"),
        (r"\bhono\b", "hono"),
        (r"\bfastify\b", "fastify"),
        (r"\bgraphql\b", "graphql"),
        (r"\brest\b", "rest"),
        # Other tech
        (r"\bgit\b", "git"),
        (r"\bai\b|\bllm\b|\bgpt\b", "ai"),
        (r"\bapi\b", "api"),
        (r"\bauth\b|\bauthentication\b", "auth"),
        (r"\bcache\b|\bcaching\b", "caching"),
        (r"\bsearch\b", "search"),
        (r"\bindex\b|\bindexing\b", "indexing"),
    ]

    def generate_tags(
        self, session: Session, messages: Optional[list] = None
    ) -> list[str]:
        """
        Generate tags from session content using pattern matching.

        Args:
            session: Session object with metadata
            messages: Optional list of message dicts with 'content' and 'role' keys

        Returns:
            List of tags (max 15), sorted by relevance
        """
        tag_scores = {}  # tag -> score (higher = more relevant)

        # Combine all text content
        text_parts = []

        # Add session metadata
        if session.title:
            text_parts.append(session.title)
        if session.first_prompt:
            text_parts.append(session.first_prompt)
        if session.last_prompt:
            text_parts.append(session.last_prompt)
        if session.last_response:
            text_parts.append(session.last_response)

        # Add message content
        if messages:
            for msg in messages:
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    text = extract_text_content(content)
                    if text:
                        text_parts.append(text)

        # Combine and normalize
        full_text = " ".join(text_parts)
        full_text_lower = full_text.lower()

        # Extract tools
        for pattern, tag_template in self.TOOL_PATTERNS:
            matches = re.finditer(pattern, full_text, re.IGNORECASE)
            for match in matches:
                if "{0}" in tag_template:
                    tag = tag_template.format(match.group(1).lower())
                else:
                    tag = tag_template
                tag_scores[tag] = tag_scores.get(tag, 0) + 2

        # Extract activities
        for pattern, tag in self.ACTIVITY_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                tag_scores[tag] = tag_scores.get(tag, 0) + 1.5

        # Extract technologies
        for pattern, tag in self.TECH_PATTERNS:
            matches = re.finditer(pattern, full_text, re.IGNORECASE)
            for _ in matches:
                tag_scores[tag] = tag_scores.get(tag, 0) + 1

        # Add project name as tag if available
        if session.project_name:
            project_tag = f"project:{session.project_name.lower()}"
            tag_scores[project_tag] = tag_scores.get(project_tag, 0) + 0.5

        # Add harness as tag
        if session.harness:
            harness_tag = f"harness:{session.harness.lower()}"
            tag_scores[harness_tag] = tag_scores.get(harness_tag, 0) + 0.5

        # Sort by score (descending) and take top 15
        sorted_tags = sorted(tag_scores.items(), key=lambda x: x[1], reverse=True)
        result = [tag for tag, _ in sorted_tags[:15]]

        return result
