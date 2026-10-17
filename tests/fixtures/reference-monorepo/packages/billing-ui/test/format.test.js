const test = require("node:test");
const assert = require("node:assert");

test("formats cents", () => {
  assert.strictEqual((1234 / 100).toFixed(2), "12.34");
});
