import MarkdownIt from "markdown-it";

const markdown = new MarkdownIt({html: false, linkify: true, breaks: false});
// 对话中的材料只能从采集范围打开，消息中的图片只展示说明文字。
markdown.renderer.rules.image = (tokens, index) => markdown.utils.escapeHtml(tokens[index]!.content || "图片");
const linkOpen = markdown.renderer.rules.link_open;
markdown.renderer.rules.link_open = (tokens, index, options, env, self) => {
  tokens[index]!.attrSet("rel", "noreferrer noopener");
  return linkOpen ? linkOpen(tokens, index, options, env, self) : self.renderToken(tokens, index, options);
};

/** html:false 和 MarkdownIt 的链接校验保证输出不执行消息中的 HTML/脚本。 */
export function renderMarkdown(content: string): string { return markdown.render(content); }
