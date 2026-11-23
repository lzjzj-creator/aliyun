<script setup>
// #/onboarding 注册后初始化表单（v5 §0 字段定稿）：
// 必填=年级/学校/专业 + 基本信息* + 教育背景*（学历/就读时间）；
// 选填=城市/GitHub/博客/GPA/主修课/照片/项目经历/技能。提交进 init_profile。
import { onMounted, reactive, ref } from "vue";
import { getMe, putInit } from "../api/auth";
import UiIcon from "../components/UiIcon.vue";
import { prepareResumePhoto } from "../utils/resumePhoto";

const emit = defineEmits(["done", "skip"]);

const form = reactive({
  grade: "", school: "", major: "", degree: "本科", period: "",
  name: "", phone: "", email: "", target_role: "",
  city: "", github: "", blog: "", gpa_or_rank: "", courses: "",
  experiences: "", skills: "",
});
const busy = ref(false);
const err = ref("");
const fieldErrs = ref({});
const showOptional = ref(false);
const booted = ref(false);
const photo = ref(null);              // {data_url} ≤30KB JPEG（init_profile.photo）
const photoErr = ref("");
const photoBusy = ref(false);

// 回显已有 init_profile（设置页复用或刷新重进）
// 键名与后端播种/预填对齐：basics.github/blog、education.gpa_or_rank、material_notes
onMounted(async () => {
  try {
    const me = await getMe();
    const p = me.init_profile || {};
    const edu = (p.education || [])[0] || {};
    const b = p.basics || {};
    const notes = p.material_notes || {};
    form.grade = p.identity?.grade || "";
    form.school = edu.school || ""; form.major = edu.major || "";
    form.degree = edu.degree || "本科"; form.period = edu.period || "";
    form.name = b.name || ""; form.phone = b.phone || ""; form.email = b.email || "";
    form.target_role = b.target_role || ""; form.city = b.city || "";
    form.github = b.github || ""; form.blog = b.blog || "";
    form.gpa_or_rank = edu.gpa_or_rank || ""; form.courses = edu.courses || "";
    form.experiences = notes.experiences || "";
    form.skills = notes.skills || "";
    if (p.photo?.data_url) photo.value = p.photo;
    if (form.city || form.github || form.blog || form.gpa_or_rank || form.courses ||
        form.experiences || form.skills) showOptional.value = true;
  } catch { /* 未认证/无档案则空表单 */ }
  booted.value = true;
});

async function onPhotoPick(e) {
  const file = e.target.files?.[0];
  e.target.value = "";
  if (!file) return;
  photoErr.value = "";
  photoBusy.value = true;
  try {
    photo.value = { data_url: await prepareResumePhoto(file) };
  } catch (ex) {
    photoErr.value = ex.message || "照片处理失败";
  } finally {
    photoBusy.value = false;
  }
}

const REQUIRED = [
  ["grade", "年级"], ["name", "姓名"], ["phone", "电话"], ["email", "邮箱"],
  ["target_role", "求职意向"], ["school", "学校"], ["major", "专业"],
  ["period", "就读时间"],
];

async function submit() {
  fieldErrs.value = {};
  for (const [k, label] of REQUIRED) {
    if (!String(form[k] || "").trim()) fieldErrs.value[k] = `${label}必填`;
  }
  if (form.email.trim() && !form.email.includes("@")) fieldErrs.value.email = "邮箱格式不正确";
  if (Object.keys(fieldErrs.value).length) { err.value = "请补全必填项"; return; }
  busy.value = true;
  err.value = "";
  try {
    await putInit({
      identity: { grade: form.grade.trim() },
      basics: {
        name: form.name.trim(), phone: form.phone.trim(), email: form.email.trim(),
        target_role: form.target_role.trim(), city: form.city.trim(),
        github: form.github.trim(), blog: form.blog.trim(),
      },
      education: [{
        school: form.school.trim(), major: form.major.trim(),
        degree: form.degree, period: form.period.trim(),
        gpa_or_rank: form.gpa_or_rank.trim(), courses: form.courses.trim(),
      }],
      material_notes: {
        experiences: form.experiences.trim(),
        skills: form.skills.trim(),
      },
      ...(photo.value ? { photo: photo.value } : {}),
    });
    emit("done");
  } catch (e) {
    if (e.status === 422 && Array.isArray(e.issues)) {
      const map = {
        "identity.grade": "grade", "basics.name": "name", "basics.phone": "phone",
        "basics.email": "email", "basics.target_role": "target_role",
        "education.0.school": "school", "education.0.major": "major",
        "education.0.degree": "degree", "education.0.period": "period",
      };
      for (const i of e.issues) fieldErrs.value[map[i.path] || i.path] = i.label + (i.code === "format" ? "格式不正确" : "必填");
      err.value = "请补全必填项";
    } else {
      err.value = `保存失败：${e.message}`;
    }
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <section class="ob-page">
    <div class="ob-card">
      <h1 class="ob-title"><UiIcon name="clipboard-list" :size="18" /> 初始化你的画像</h1>
      <p class="ob-sub">一次填写，访谈不再追问这些基础信息；带 * 必填，其余可留空。</p>

      <form v-if="booted" @submit.prevent="submit" class="ob-form">
        <fieldset>
          <legend>基本信息</legend>
          <div class="ob-grid">
            <label>姓名 *<input v-model="form.name" :disabled="busy" /><em v-if="fieldErrs.name">{{ fieldErrs.name }}</em></label>
            <label>电话 *<input v-model="form.phone" :disabled="busy" /><em v-if="fieldErrs.phone">{{ fieldErrs.phone }}</em></label>
            <label>邮箱 *<input v-model="form.email" type="email" :disabled="busy" /><em v-if="fieldErrs.email">{{ fieldErrs.email }}</em></label>
            <label>求职意向 *<input v-model="form.target_role" placeholder="如：后端开发实习生" :disabled="busy" /><em v-if="fieldErrs.target_role">{{ fieldErrs.target_role }}</em></label>
          </div>
        </fieldset>

        <fieldset>
          <legend>教育背景</legend>
          <div class="ob-grid">
            <label>学校 *<input v-model="form.school" :disabled="busy" /><em v-if="fieldErrs.school">{{ fieldErrs.school }}</em></label>
            <label>专业 *<input v-model="form.major" :disabled="busy" /><em v-if="fieldErrs.major">{{ fieldErrs.major }}</em></label>
            <label>年级 *<input v-model="form.grade" placeholder="如：大三" :disabled="busy" /><em v-if="fieldErrs.grade">{{ fieldErrs.grade }}</em></label>
            <label>学历 *
              <select v-model="form.degree" :disabled="busy">
                <option>本科</option><option>硕士</option><option>博士</option>
                <option>大专</option><option>其他</option>
              </select>
            </label>
            <label>就读时间 *<input v-model="form.period" placeholder="如：2023.09 – 2027.06" :disabled="busy" /><em v-if="fieldErrs.period">{{ fieldErrs.period }}</em></label>
          </div>
        </fieldset>

        <button type="button" class="ob-more" :aria-expanded="showOptional"
                @click="showOptional = !showOptional">
          选填信息（城市 / 链接 / GPA / 经历素材）
          <span class="ob-arrow" :class="{ open: showOptional }">›</span>
        </button>
        <fieldset v-if="showOptional" class="ob-optional">
          <div class="ob-grid">
            <label>城市<input v-model="form.city" :disabled="busy" /></label>
            <label>GPA / 排名<input v-model="form.gpa" placeholder="如：3.6/4.0，前 15%" :disabled="busy" /></label>
            <label>GitHub<input v-model="form.github" placeholder="https://github.com/…" :disabled="busy" /></label>
            <label>博客 / 作品集<input v-model="form.blog" placeholder="https://…" :disabled="busy" /></label>
          </div>
          <label class="ob-wide">主修课程<input v-model="form.courses" placeholder="如：数据结构、操作系统、数据库…" :disabled="busy" /></label>
          <label class="ob-wide">项目 / 实习 / 竞赛等经历素材<textarea v-model="form.experiences" rows="3" :disabled="busy"
              placeholder="简单罗列即可，访谈时还能深挖：项目名、角色、时间、做了什么、成果…"></textarea></label>
          <label class="ob-wide">专业技能 / 工具<textarea v-model="form.skills" rows="2" :disabled="busy"
              placeholder="如：Python、SQL、Figma、英语六级…"></textarea></label>
          <label class="ob-wide">照片（可选）
            <span class="ob-photo">
              <img v-if="photo" :src="photo.data_url" alt="证件照预览" class="ob-photo-img" />
              <input type="file" accept="image/jpeg,image/png" :disabled="busy || photoBusy"
                     @change="onPhotoPick" />
              <em v-if="photoErr">{{ photoErr }}</em>
            </span>
          </label>
        </fieldset>

        <p class="ob-privacy">初始化资料存于服务端账号下，随账号删除而清空。</p>
        <p v-if="err" class="ob-err" role="alert">{{ err }}</p>
        <button class="ob-submit" type="submit" :disabled="busy || photoBusy">
          {{ busy ? "保存中…" : "保存并开始" }}
        </button>
        <button type="button" class="ob-skip" :disabled="busy" @click="emit('skip')">
          先跳过，稍后再填
        </button>
      </form>
      <p v-else class="ob-sub">加载中…</p>
    </div>
  </section>
</template>

<style scoped>
.ob-page { padding: 24px 16px; max-width: 640px; margin: 0 auto; }
.ob-card {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 26px 24px; box-shadow: var(--shadow-sm);
}
.ob-title { font-size: 1.1rem; margin: 0 0 4px; display: flex; align-items: center; gap: 8px; }
.ob-sub { font-size: .8rem; color: var(--ink-secondary); margin: 0 0 18px; }
fieldset { border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin: 0 0 14px; }
legend { font-size: .82rem; font-weight: 600; color: var(--ink-secondary); padding: 0 6px; }
.ob-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.ob-grid label, .ob-wide { display: block; font-size: .78rem; color: var(--ink-secondary); }
.ob-wide { margin-top: 10px; }
input, select, textarea {
  display: block; width: 100%; margin-top: 4px; padding: 9px 11px; box-sizing: border-box;
  border: 1px solid var(--border); border-radius: 8px; font: inherit; font-size: .88rem;
  background: var(--bg-surface); color: var(--ink);
}
textarea { resize: vertical; }
input:focus, select:focus, textarea:focus { outline: 2px solid var(--brand-soft, rgba(59,130,246,.3)); border-color: var(--brand); }
em { display: block; font-style: normal; color: var(--danger); font-size: .7rem; margin-top: 2px; }
.ob-more {
  width: 100%; text-align: left; padding: 10px 12px; margin-bottom: 14px;
  border: 1px dashed var(--border); border-radius: 10px; background: none;
  font: inherit; font-size: .82rem; color: var(--ink-secondary); cursor: pointer;
  display: flex; justify-content: space-between; align-items: center;
}
.ob-more:hover { color: var(--brand); border-color: var(--brand); }
.ob-arrow { transition: transform .15s; }
.ob-arrow.open { transform: rotate(90deg); }
.ob-photo { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
.ob-photo input[type=file] { margin-top: 0; padding: 6px 0; border: none; background: none; font-size: .78rem; }
.ob-photo-img { width: 48px; height: 60px; object-fit: cover; border-radius: 6px; border: 1px solid var(--border); }
.ob-privacy { font-size: .72rem; color: var(--ink-muted); margin: 0 0 10px; }
.ob-err { color: var(--danger); font-size: .8rem; }
.ob-skip {
  width: 100%; margin-top: 8px; padding: 9px; border: none; border-radius: 10px;
  background: none; cursor: pointer; font: inherit; font-size: .8rem; color: var(--ink-muted);
}
.ob-skip:hover { color: var(--ink); }
.ob-submit {
  width: 100%; padding: 11px; border: none; border-radius: 10px; cursor: pointer;
  background: var(--brand); color: #fff; font: inherit; font-weight: 600; font-size: .9rem;
}
.ob-submit:disabled { opacity: .6; }
@media (max-width: 560px) { .ob-grid { grid-template-columns: 1fr; } }
</style>
