import { expect, test, type Page } from "@playwright/test";

const questionInput = (page: Page) => page.getByPlaceholder(/질문 입력/);

test("real LLM executes Python, reuses worker state, and renders a Plotly analysis card", async ({ page }) => {
  test.skip(
    process.env.REPL_E2E_LIVE !== "1",
    "set REPL_E2E_LIVE=1 with OpenRouter and MongoDB configured",
  );

  await page.goto("/");
  await page.getByRole("button", { name: "세션 시작" }).click();
  await expect(page.getByText(/세션 [0-9a-f]{8}/)).toBeVisible({ timeout: 60_000 });

  await questionInput(page).fill(
    "wafer 단위로 중복 제거한 fail_value의 평균과 표준편차를 계산하고 히스토그램을 emit_plot으로 반드시 보여준 뒤 가설 검증 관점에서 판정해줘. 같은 Python worker 상태 재사용 검증을 위해 e2e_state_marker = 41 변수도 저장해줘",
  );
  await page.getByRole("button", { name: "보내기" }).click();

  const firstCard = page.locator(".analysis-card").nth(0);
  await expect(page.locator(".analysis-card")).toHaveCount(1);
  await expect(firstCard.getByText("run_python")).toBeVisible({ timeout: 180_000 });
  await expect(firstCard.locator("pre.code")).toContainText("emit_plot", { timeout: 180_000 });
  await expect(firstCard.locator(".result-status")).toContainText("success", { timeout: 180_000 });
  await expect(firstCard.locator(".js-plotly-plot")).toBeVisible({ timeout: 180_000 });
  await expect(firstCard.locator(".analysis-answer").getByText(/판정|관찰/)).toBeVisible({
    timeout: 180_000,
  });
  await expect(firstCard.locator(".analysis-status")).toHaveText("완료", { timeout: 180_000 });

  await questionInput(page).fill(
    "이전 Python 실행에서 저장한 e2e_state_marker에 1을 더한 값만 run_python에서 print(e2e_state_marker + 1)로 출력해서 worker 상태가 유지됐는지 확인해줘",
  );
  await page.getByRole("button", { name: "보내기" }).click();

  const secondCard = page.locator(".analysis-card").nth(1);
  await expect(page.locator(".analysis-card")).toHaveCount(2);
  await expect(secondCard.getByText("run_python")).toBeVisible({ timeout: 180_000 });
  await expect(secondCard.locator(".result-status")).toHaveText("success", { timeout: 180_000 });
  await expect(
    secondCard.locator(".result-output section").filter({ hasText: "stdout" }).locator("pre"),
  ).toHaveText("42", { timeout: 180_000 });
  await expect(secondCard.locator(".analysis-status")).toHaveText("완료", { timeout: 180_000 });

  await page.getByRole("button", { name: "새 세션" }).click();
  await expect(page.getByRole("button", { name: "세션 시작" })).toBeVisible({ timeout: 30_000 });
});
