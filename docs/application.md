# Application and interview kit

## Hebrew application paragraph

Use this after running the demo and reviewing the code. Add your actual repository/demo link; do not invent prior work experience.

> לקראת הגשת המועמדות לתפקיד מפתח/ת DATA בחטיבת משאבי אנוש, הכנתי פרויקט פורטפוליו בשם PeopleOps Control, המבוסס על נתונים סינתטיים. הפרויקט מדגים תהליך ETL ב־Python ו־Pandas לאיחוד נתוני עובדים, נוכחות ושכר, בקרות איכות והתאמות שכר, שמירת היסטוריית ריצות והפקת דוחות Excel אוטומטיים. הוא כולל גם סכמת MSSQL, שאילתות עם CTE ופונקציות חלון, וממשק API עם טעינה מדורגת ו־retry. בדמו אפשר לראות כיצד נתונים שגויים חוסמים פרסום, איך נשמרת הגרסה התקינה האחרונה, וכיצד ריצה חוזרת נמנעת מכפילות. אשמח להציג את הפתרון ואת שיקולי התכנון בראיון. קישור לפרויקט: https://github.com/AmnonTamsut/peopleops-control

Keep the SQL execution caveat in the repository. Say "includes an MSSQL integration" rather than claiming an operational production deployment.

## A five-minute walkthrough

**0:00 — The business problem.** "HR, attendance and payroll can disagree. My goal is to give payroll operations a traceable exception list before they rely on a report."

**0:45 — One discrepancy.** Show the blocked September run and inspect one employee. Explain expected gross with the independent worked example in `business-rules.md`, then point out the control tolerance and its demonstration-only status.

**1:45 — Evidence people can use.** Download Excel. Show the owner and correction instruction, and the source checksum. Explain why a specific run's export cannot quietly change when a later run appears.

**2:30 — Recovery.** Run corrected sources, then repeat the same run. Show the changed checksum/new run on correction and the reused result on replay. Explain that you never patch old evidence.

**3:30 — SQL and performance.** Open `sql/` and discuss period-based indexes, window calculations and the transaction that changes the publication pointer. Be candid that the local demo uses SQLite and SQL Server requires integration validation.

**4:30 — Next steps.** Explain how you would agree source contracts with HR/payroll, define critical thresholds with business owners, connect the real systems and add permissions/monitoring. Do not claim this simplified gross calculation handles employment law.

## Questions to prepare for

| Question | Explain using this project |
|---|---|
| Why not join raw source tables directly? | Duplicate keys can amplify payroll sums; validate cardinality and quarantine invalid records first. |
| What happens when the same scheduled job runs twice? | A source checksum and rule version identify existing immutable evidence; SQL publication also has transactional protection. |
| What happens when an API fails halfway? | No partial employee snapshot is returned; bounded retries handle only transient failures. |
| Why integer cents? | Avoid binary float differences in monetary reconciliation; round Decimal once with an explicit policy. |
| What is actually published? | A pointer to a monthly run that passed critical controls. It is a data control decision, not payroll authorization. |
| How do you optimize SQL? | Filter by period, use the intended covering indexes, compare plans/logical reads, and avoid claiming speed gains without measurements. |
| Where is the biggest limitation? | Synthetic inputs and a simplified business rule; no live MSSQL verification or production auth in the demo. |

## Before sending

Run the recovery demonstration yourself. Read the pipeline and SQL transaction. Publish the source to your own GitHub repository when ready, replace the link placeholder, and describe your actual contribution accurately. This project can demonstrate skills; it cannot replace the advertised experience requirement.
