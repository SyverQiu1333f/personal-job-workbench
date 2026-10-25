export function pdfErrorText(error){
 const messages={
  PasswordException:'这份 PDF 需要打开密码，当前页面无法直接预览。请下载原件后使用本地阅读器打开。',
  InvalidPDFException:'这份 PDF 的结构无法解析，可能已经损坏。请下载原件核对，或重新导入有效文件。',
  MissingPDFException:'未找到这份 PDF 原件，请刷新页面，必要时从完整备份恢复。',
  UnexpectedResponseException:'读取 PDF 时遇到异常响应，请刷新页面后重试。',
  LocalReadError:'原件读取未完成，可能资料已恢复或原件不可用。请刷新页面，仍失败时检查完整备份。'
 };
 return messages[error?.name]||'这份 PDF 暂时无法预览。请刷新后重试，或下载原件使用本地阅读器查看。';
}
