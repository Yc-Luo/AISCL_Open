import api from './client'

export interface Course {
    id: string
    name: string
    semester: string
    teacher_id: string
    description?: string
    students: string[]
    invite_code: string
    experiment_template_key?: string
    experiment_template_label?: string
    experiment_template_release_id?: string
    experiment_template_release_note?: string
    experiment_template_source?: string
    experiment_template_bound_at?: string
    initial_task_document_title?: string
    initial_task_document_content?: string
    created_at: string
}

export interface ExperimentTemplateOption {
    key: string
    label: string
    source?: string
    release_id?: string
    release_note?: string
    group_condition?: string
    ai_mode?: 'single_agent' | 'multi_agent'
    process_mode?: 'on' | 'off'
    rule_set?: string
    stage_sequence?: string[]
    teacher_summary?: string
}

export interface CourseAISettings {
    ai_scaffold_mode: 'single_agent' | 'multi_agent'
    process_scaffold_mode: 'on' | 'off'
    collaboration_memory_enabled: boolean
    auto_prompt_enabled: boolean
}

export type BuiltinRoleId = 'evidence_researcher' | 'viewpoint_challenger' | 'feedback_prompter' | 'problem_progressor'

export interface BuiltinLearningDesignRole {
    id: BuiltinRoleId
    enabled: boolean
    prompt: string
}

export interface CustomLearningDesignRole {
    id: `custom_${string}`
    enabled: boolean
    prompt: string
    name: string
    routing_anchor: BuiltinRoleId
}

export type LearningDesignRole = BuiltinLearningDesignRole | CustomLearningDesignRole

export interface StageItem {
    id: 'problem_construction' | 'meaning_exploration' | 'explanation_integration' | 'application_solution'
    name: string
    activity_guidance: string
}

export interface LearningDesign {
    roles: LearningDesignRole[]
    orchestration_mode: 'adaptive' | 'single' | 'parallel' | 'debate' | 'pipeline'
    stages: StageItem[]
}

export interface DesignSnapshot extends LearningDesign {
    revision: string
}

export interface Student {
    id: string
    username: string
    email: string
    avatar_url?: string
}

export interface StudentImportItem {
    username: string
    email: string
    password?: string
}

export interface StudentImportResult {
    row: number
    username: string
    email: string
    status: 'created' | 'linked' | 'skipped' | 'failed'
    message: string
    user_id?: string
}

export interface StudentImportResponse {
    created_count: number
    linked_count: number
    skipped_count: number
    failed_count: number
    results: StudentImportResult[]
}

export const courseService = {
    getCourses: async (): Promise<Course[]> => {
        const response = await api.get('/courses')
        return response.data.courses // Backend returns { courses: [...] }
    },

    joinCourse: async (inviteCode: string): Promise<{ message: string; course_id: string }> => {
        const response = await api.post('/courses/join', { invite_code: inviteCode.trim().toUpperCase() })
        return response.data
    },

    getExperimentTemplates: async (): Promise<ExperimentTemplateOption[]> => {
        const response = await api.get('/courses/experiment-templates')
        return response.data.templates || []
    },

    createCourse: async (data: { name: string; semester: string; description?: string; initial_task_document_title?: string; initial_task_document_content?: string }) => {
        const response = await api.post('/courses', data)
        return response.data
    },

    deleteCourse: async (courseId: string) => {
        await api.delete(`/courses/${courseId}`)
    },

    updateCourse: async (courseId: string, data: { name?: string; semester?: string; description?: string; initial_task_document_title?: string; initial_task_document_content?: string }) => {
        const response = await api.put(`/courses/${courseId}`, data)
        return response.data
    },

    getCourseAISettings: async (courseId: string): Promise<CourseAISettings | null> => {
        const response = await api.get<{ settings: CourseAISettings | null }>(`/courses/${encodeURIComponent(courseId)}/ai-settings`)
        return response.data.settings
    },

    updateCourseAISettings: async (courseId: string, settings: CourseAISettings): Promise<CourseAISettings | null> => {
        const response = await api.put<{ settings: CourseAISettings | null }>(`/courses/${encodeURIComponent(courseId)}/ai-settings`, {
            ai_scaffold_mode: settings.ai_scaffold_mode,
            process_scaffold_mode: settings.process_scaffold_mode,
            collaboration_memory_enabled: settings.collaboration_memory_enabled,
            auto_prompt_enabled: settings.auto_prompt_enabled,
        })
        return response.data.settings
    },

    getCourseStudents: async (courseId: string): Promise<Student[]> => {
        const response = await api.get(`/courses/${courseId}/students`)
        return response.data.students
    },

    getLearningDesign: async (courseId: string): Promise<DesignSnapshot | null> => {
        const response = await api.get<{ design: DesignSnapshot | null }>(`/courses/${encodeURIComponent(courseId)}/learning-design`)
        return response.data.design
    },

    saveLearningDesign: async (courseId: string, design: LearningDesign): Promise<DesignSnapshot | null> => {
        const response = await api.put<{ design: DesignSnapshot | null }>(`/courses/${encodeURIComponent(courseId)}/learning-design`, design)
        return response.data.design
    },

    applyLearningDesign: async (courseId: string, projectId: string): Promise<{ project_id: string; revision: string }> => {
        const response = await api.post<{ project_id: string; revision: string }>(`/courses/${encodeURIComponent(courseId)}/learning-design/projects/${encodeURIComponent(projectId)}`)
        return response.data
    },

    removeStudent: async (courseId: string, studentId: string) => {
        await api.delete(`/courses/${courseId}/students/${studentId}`)
    },

    addStudentToCourse: async (courseId: string, studentId: string) => {
        const response = await api.post(`/courses/${courseId}/students?student_id=${studentId}`)
        return response.data
    },

    bulkImportStudents: async (
        courseId: string,
        data: { students: StudentImportItem[]; default_password?: string }
    ): Promise<StudentImportResponse> => {
        const response = await api.post(`/courses/${courseId}/students/bulk-import`, data)
        return response.data
    },

    getStudentProgress: async (studentId: string) => {
        const response = await api.get(`/analytics/projects/student/${studentId}/progress`)
        return response.data
    }
}
