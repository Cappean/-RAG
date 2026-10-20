import { expect, test } from '@playwright/test'

test('real persisted failed technical turn is not displayed as completed', async ({ page, request }, testInfo) => {
  const patients = await (await request.get('http://127.0.0.1:8000/api/patients')).json()
  const technicalPatientIds = patients.filter((item: { name: string }) => item.name.startsWith('UI技术验证-')).map((item: { id: string }) => item.id)
  const list = await (await request.get('http://127.0.0.1:8000/api/conversations')).json()
  let failedIndex = -1
  for (const [index, conversation] of list.entries()) {
    if (!technicalPatientIds.includes(conversation.patientId)) continue
    const history = await (await request.get(`http://127.0.0.1:8000/api/conversations/${conversation.id}/messages`)).json()
    if (history.some((message: { role: string; status: string }) => message.role === 'assistant' && message.status === 'failed')) { failedIndex = index; break }
  }
  test.skip(failedIndex < 0, 'No existing failed UI technical turn; does not manufacture server failures')
  await page.goto('/')
  await expect(page.locator('.history-item')).toHaveCount(list.length, { timeout: 30_000 })
  await page.locator('.history-item').nth(failedIndex).click()
  await expect(page.locator('.message-card.assistant .message-heading').filter({ hasText: '生成失败' })).toBeVisible()
  await expect(page.getByText('本轮回答未完成；请查看错误提示，确认服务恢复后重新发送问题。')).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('real-failed-history-explicit-state.png'), fullPage: true })
})

test('real existing technical MD original opens without invented location', async ({ page }, testInfo) => {
  await page.goto('/')
  const document = page.locator('.document-row').filter({ hasText: /UI技术验证-\d+\.md/ }).first()
  await expect(document).toBeVisible({ timeout: 30_000 })
  await document.getByRole('button', { name: '查看原文' }).click()
  await expect(page.locator('.markdown-original code').first()).toContainText('UI技术验证-', { timeout: 30_000 })
  await expect(page.locator('.viewer-source-note')).toContainText('没有可用行号')
  await expect(page.locator('.line-highlight')).toHaveCount(0)
  await expect(page.locator('.source-scroll img, .source-scroll script, .source-scroll iframe')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('real-md-original.png'), fullPage: true })
})

test('real historical PDF citation opens actual page and normalized regions', async ({ page, request }, testInfo) => {
  const conversationId = process.env.DEMO_PDF_CONVERSATION_ID
  const documentId = process.env.DEMO_PDF_DOCUMENT_ID
  test.skip(!conversationId || !documentId, 'Requires an existing real PDF citation from successful technical verification')
  const list = await (await request.get('http://127.0.0.1:8000/api/conversations')).json()
  const index = list.findIndex((item: { id: string }) => item.id === conversationId)
  expect(index).toBeGreaterThanOrEqual(0)
  const history = await (await request.get(`http://127.0.0.1:8000/api/conversations/${conversationId}/messages`)).json()
  const citations = history.flatMap((message: { citations: Array<{ documentId: string; quote: string }> }) => message.citations).filter((item: { documentId: string }) => item.documentId === documentId)
  // Verify a medical body paragraph rather than incidental cover/version/toc text.
  const citation = citations.find((item: { chunkId: string }) => item.chunkId === process.env.DEMO_PDF_CHUNK_ID)
    ?? citations.find((item: { quote: string }) => item.quote.includes('指南对胃全切后是否要做胃镜'))
    ?? citations.find((item: { quote: string }) => /肿瘤标记物|幽门螺旋杆菌|MSI\/MMR/.test(item.quote))
    ?? citations[0]
  expect(citation).toBeTruthy()
  const target = citation.sourceLocations.find((location: { pageNumber?: number }) => location.pageNumber != null)
  expect(target).toBeTruthy()
  await page.goto('/')
  await expect(page.locator('.history-item')).toHaveCount(list.length, { timeout: 30_000 })
  await page.locator('.history-item').nth(index).click()
  await page.locator('.message-card.assistant .evidence-card').filter({ hasText: citation.quote.slice(0, 40) }).first().click()
  const canvas = page.locator('.pdf-canvas-wrap canvas')
  await expect(canvas).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.viewer-source-note')).toContainText('已定位物理页', { timeout: 30_000 })
  await expect(page.locator('.viewer-toolbar')).toContainText(`${target.pageNumber} /`)
  const canvasState = await canvas.evaluate((element: HTMLCanvasElement) => {
    const pixels = element.getContext('2d')!.getImageData(0, 0, element.width, element.height).data
    let inkPixels = 0
    for (let index = 0; index < pixels.length; index += 4) {
      if (pixels[index]! < 220 || pixels[index + 1]! < 220 || pixels[index + 2]! < 220) inkPixels++
    }
    return { width: element.width, height: element.height, pixels: inkPixels }
  })
  expect(canvasState.width).toBeGreaterThan(100)
  expect(canvasState.height).toBeGreaterThan(100)
  expect(canvasState.pixels).toBeGreaterThan(100)
  const boxes = citation.sourceLocations.filter((location: { pageNumber?: number; bbox?: number[] }) => location.pageNumber === target.pageNumber && location.bbox?.length === 4)
  await expect(page.locator('.source-bbox')).toHaveCount(boxes.length)
  const geometry = await page.locator('.source-bbox').evaluateAll(elements => elements.map(element => {
    const rectangle = element.getBoundingClientRect()
    const parent = element.parentElement!.getBoundingClientRect()
    return { x: (rectangle.x - parent.x) / parent.width, y: (rectangle.y - parent.y) / parent.height, width: rectangle.width / parent.width, height: rectangle.height / parent.height }
  }))
  for (const [index, rectangle] of geometry.entries()) {
    const box = boxes[index].bbox
    expect(rectangle.x).toBeCloseTo(box[0], 2)
    expect(rectangle.y).toBeCloseTo(box[1], 2)
    expect(rectangle.width).toBeCloseTo(box[2] - box[0], 2)
    expect(rectangle.height).toBeCloseTo(box[3] - box[1], 2)
  }
  await page.getByRole('dialog', { name: '引用原文' }).screenshot({ path: testInfo.outputPath('real-pdf-citation-page-bbox.png') })
  await testInfo.attach('pdf-reader-evidence', { body: JSON.stringify({ conversationId, documentId, quote: citation.quote, targetPage: target.pageNumber, sourceLocations: citation.sourceLocations, canvasState, geometry }, null, 2), contentType: 'application/json' })
})
