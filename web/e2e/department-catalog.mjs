import { chromium } from "playwright-core";

const BASE = process.env.BASE;
const BROWSER_BIN = process.env.BROWSER_BIN;
const TEACHER_PASSWORD = process.env.E2E_TEACHER_PASSWORD;
const STUDENT_PASSWORD = process.env.E2E_STUDENT_PASSWORD;
if (!BASE || !BROWSER_BIN || !TEACHER_PASSWORD || !STUDENT_PASSWORD) {
  throw new Error("BASE, BROWSER_BIN and synthetic local account passwords are required");
}

const checks = [];
const errors = [];
const check = (name, passed, detail = "") => {
  checks.push({ name, passed, detail });
  if (!passed) throw new Error(`browser check failed: ${name}`);
};
const browser = await chromium.launch({ executablePath: BROWSER_BIN, args: ["--no-sandbox"] });
const context = await browser.newContext({ viewport: { width: 1365, height: 900 }, locale: "zh-CN" });
const page = await context.newPage();
page.on("console", (message) => {
  if (message.type() === "error" && !/status of 404/.test(message.text())) errors.push(message.text());
});
page.on("pageerror", (error) => errors.push(String(error)));

async function login(username, password) {
  await page.goto(BASE + "/");
  await page.getByRole("heading", { name: "登录" }).waitFor();
  await page.getByLabel("账号").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByRole("button", { name: "退出" }).waitFor();
}

try {
  await login("catalog-teacher", TEACHER_PASSWORD);
  await page.goto(BASE + "/#/teacher/cases");
  await page.getByRole("heading", { name: "病例管理" }).waitFor();
  await page.locator(".list-item").first().click();
  const department = page.locator("label.field").filter({ has: page.getByText("科室", { exact: true }) }).locator("select");
  await department.selectOption("05");
  const subspecialty = page.locator("label.field").filter({ has: page.getByText("亚专科", { exact: true }) }).locator("select");
  await subspecialty.selectOption("05.01");
  check(
    "教师页面可选择新启用的分类",
    (await department.inputValue()) === "05" && (await subspecialty.inputValue()) === "05.01",
    "选择仅保留在页面状态，未保存或更改任何病例",
  );

  await page.getByRole("button", { name: "退出" }).click();
  await page.getByRole("heading", { name: "登录" }).waitFor();
  await login("catalog-student", STUDENT_PASSWORD);
  await page.goto(BASE + "/#/library");
  await page.getByRole("heading", { name: "病例库" }).waitFor();
  const studentDepartment = page.getByLabel("科室", { exact: true });
  const unfilteredCaseRows = await page.locator(".table tbody tr").count();
  check("开发模式病例库仍有五个当前病例行", unfilteredCaseRows === 5, `结果行数 ${unfilteredCaseRows}`);
  const emptySpecialtyOptionCount = await page.locator('select[aria-label="科室"] option[value="05"]').count();
  check("学生页面不显示无病例的分类", emptySpecialtyOptionCount === 0);
  await studentDepartment.selectOption("03");
  await page.getByLabel("亚专科", { exact: true }).selectOption("03.04");
  const visibleRows = await page.locator(".table tbody tr").count();
  check("学生页面按已有病例分类筛选", visibleRows > 0, `结果行数 ${visibleRows}`);
  check("浏览器无前端脚本错误", errors.length === 0, errors.slice(0, 2).join(" | ") || "无");

  console.log("BROWSER_RESULT=" + JSON.stringify({
    browser: browser.version(),
    checks,
    teacher_selected: { department_code: "05", subspecialty_code: "05.01", saved: false },
    student_filter: { department_code: "03", subspecialty_code: "03.04", visible_rows: visibleRows },
    unfiltered_case_rows: unfilteredCaseRows,
    empty_enabled_specialty_absent: emptySpecialtyOptionCount === 0,
    console_errors: errors.length,
  }));
} finally {
  await browser.close();
}
