




# F:\vscode\New folder\Samsaram athu Minsaram\employee_routes.py
from flask import Blueprint, jsonify, request, session, render_template, redirect, url_for, send_from_directory
from db import get_db_connection
# UPDATED: Import the remodeled, data-driven agents
from ai_agents import profile_agent, assessment_agent, recommender_agent, tracker_agent, get_ai_course_recommendation, generate_quiz_questions
import os

employee_bp = Blueprint('employee', __name__)

# --- NEW: Route to handle course viewing with progress tracking ---
@employee_bp.route('/view_course/<course_name>')
def view_course(course_name):
    if session.get('role') != 'employee':
        return redirect('/')
    
    emp_id = session.get('emp_code')
    
    # Update course status to "In Progress" if it's "Not Started"
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Check if course is assigned to this employee
            cursor.execute("""
                SELECT status, progress FROM course_assigned 
                WHERE emp_id = %s AND course_name = %s
            """, (emp_id, course_name))
            course_assignment = cursor.fetchone()
            
            if not course_assignment:
                return jsonify({"success": False, "message": "Course not assigned to you."}), 404
            
            # Update status to "In Progress" if it's "Not Started"
            if course_assignment['status'] == 'Not Started':
                cursor.execute("""
                    UPDATE course_assigned 
                    SET status = 'In Progress', progress = 10 
                    WHERE emp_id = %s AND course_name = %s
                """, (emp_id, course_name))
                conn.commit()
            
            # Get course file path
            cursor.execute("SELECT CourseFile FROM course WHERE CourseName = %s", (course_name,))
            course_file = cursor.fetchone()
            
            if not course_file:
                return jsonify({"success": False, "message": "Course file not found."}), 404
            
            # Serve the course file
            course_file_path = os.path.join('static', 'courses', course_file['CourseFile'])
            if os.path.exists(course_file_path):
                return send_from_directory('static/courses', course_file['CourseFile'])
            else:
                return jsonify({"success": False, "message": "Course file not found on server."}), 404
                
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"Error accessing course: {str(e)}"}), 500
    finally:
        conn.close()

# --- UPDATED: Route to update course progress ---
@employee_bp.route('/update_course_progress', methods=['POST'])
def update_course_progress():
    if session.get('role') != 'employee':
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    emp_id = session.get('emp_code')
    data = request.json
    course_name = data.get('course_name')
    progress = data.get('progress', 0)
    
    if not course_name:
        return jsonify({"success": False, "message": "Course name not provided."}), 400
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Update progress
            cursor.execute("""
                UPDATE course_assigned 
                SET progress = %s, 
                    status = CASE 
                        WHEN %s >= 100 THEN 'Completed'
                        WHEN %s > 0 THEN 'In Progress'
                        ELSE status
                    END
                WHERE emp_id = %s AND course_name = %s
            """, (progress, progress, progress, emp_id, course_name))
            
            if cursor.rowcount == 0:
                return jsonify({"success": False, "message": "Course not found or not assigned to you."}), 404
            
            conn.commit()
            return jsonify({"success": True, "message": "Progress updated successfully."})
            
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"Error updating progress: {str(e)}"}), 500
    finally:
        conn.close()

# --- NEW: Route to handle course content quiz submissions ---
@employee_bp.route('/submit_course_quiz', methods=['POST'])
def submit_course_quiz():
    if session.get('role') != 'employee':
        return jsonify({"success": False, "message": "Unauthorized"}), 401
    
    emp_id = session.get('emp_code')
    data = request.json
    course_name = data.get('course_name')
    score = data.get('score', 0)
    total_questions = data.get('total_questions', 0)
    
    if not course_name or total_questions == 0:
        return jsonify({"success": False, "message": "Invalid quiz data provided."}), 400
    
    # Calculate percentage
    percentage = (score / total_questions) * 100
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Record the quiz attempt
            cursor.execute("""
                INSERT INTO assessment_marks (emp_id, course_name, marks_obtained) 
                VALUES (%s, %s, %s)
            """, (emp_id, course_name, score))
            
            # Check if score meets 80% threshold (8/10 or equivalent)
            if percentage >= 80:
                # Mark course as completed
                cursor.execute("""
                    UPDATE course_assigned 
                    SET status = 'Completed', progress = 100 
                    WHERE emp_id = %s AND course_name = %s
                """, (emp_id, course_name))
                
                # Add to completed courses if not already there
                cursor.execute("""
                    SELECT COUNT(*) as count 
                    FROM course_completed 
                    WHERE emp_id = %s AND course_name = %s
                """, (emp_id, course_name))
                
                existing = cursor.fetchone()
                if existing['count'] == 0:
                    cursor.execute("""
                        INSERT INTO course_completed (emp_id, course_name) 
                        VALUES (%s, %s)
                    """, (emp_id, course_name))
                
                message = f"Congratulations! You passed with {percentage:.1f}%. Course marked as completed."
                status = "completed"
            else:
                # Keep course in progress but don't reset
                cursor.execute("""
                    UPDATE course_assigned 
                    SET status = 'In Progress', progress = 75 
                    WHERE emp_id = %s AND course_name = %s
                """, (emp_id, course_name))
                
                message = f"You scored {percentage:.1f}%. You need 80% to complete the course. Keep studying!"
                status = "in_progress"
            
            conn.commit()
            
            return jsonify({
                "success": True, 
                "message": message,
                "status": status,
                "score": score,
                "total": total_questions,
                "percentage": percentage,
                "passed": percentage >= 80
            })
            
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"Error processing quiz: {str(e)}"}), 500
    finally:
        conn.close()

# --- UPDATED: Central API Route for All Employee-Facing Agents ---
@employee_bp.route('/ask_agent', methods=['POST'])
def ask_agent():
    if 'role' not in session or session['role'] != 'employee':
        return jsonify({"error": "Unauthorized"}), 401

    data = request.json
    agent_type = data.get('agent')
    emp_id = session['emp_code'] # Use emp_id for consistency

    # Map agent types to the new, remodeled functions
    agent_functions = {
        'profile': profile_agent,
        'assessment': assessment_agent,
        'recommender': recommender_agent,
        'tracker': tracker_agent
    }

    agent_function = agent_functions.get(agent_type)

    if not agent_function:
        return jsonify({"error": "Unknown agent"}), 400

    # Call the appropriate agent function with the employee ID
    response = agent_function(emp_id)
    return jsonify(response)


# --- Other routes remain unchanged but are included for completeness ---

@employee_bp.route('/agent/<agent_type>')
def agent_page(agent_type):
    if session.get('role') != 'employee': return redirect('/')
    valid_agents = ['profile', 'assessment', 'recommender', 'tracker']
    if agent_type not in valid_agents: return redirect('/dashboard_employee')
    return render_template('agent_page.html', agent_type=agent_type)

@employee_bp.route('/recommend_course', methods=['GET'])
def recommend_course():
    if session.get('role') != 'employee': return jsonify({"success": False, "message": "Unauthorized"}), 401
    emp_id = session.get('emp_code')
    result = get_ai_course_recommendation(emp_id)
    return jsonify(result)

@employee_bp.route('/enroll_course', methods=['POST'])
def enroll_in_course():
    if session.get('role') != 'employee': return jsonify({"success": False, "message": "Unauthorized"}), 401
    emp_id = session.get('emp_code')
    data = request.json
    course_name = data.get('course_name')
    if not course_name: return jsonify({"success": False, "message": "Course name not provided."}), 400
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            # Check if course is already assigned to this employee
            cursor.execute("SELECT assignment_id FROM course_assigned WHERE emp_id = %s AND course_name = %s", (emp_id, course_name))
            existing = cursor.fetchone()
            if existing:
                return jsonify({"success": False, "message": f"You are already enrolled in {course_name}."}), 400
            
            # Check if course exists in the course catalog
            cursor.execute("SELECT CourseName FROM course WHERE CourseName = %s", (course_name,))
            course = cursor.fetchone()
            if not course:
                return jsonify({"success": False, "message": "Course not found in catalog."}), 404
            
            # Assign the course
            cursor.execute("INSERT INTO course_assigned (emp_id, course_name, status, progress) VALUES (%s, %s, 'Not Started', 0)", (emp_id, course_name))
            conn.commit()
        return jsonify({"success": True, "message": f"Successfully enrolled in {course_name}."})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"An error occurred during enrollment: {e}"}), 500
    finally:
        conn.close()

@employee_bp.route('/my_courses')
def my_courses_page():
    if session.get('role') != 'employee': return redirect('/')
    return render_template('my_courses.html')

@employee_bp.route('/get_my_courses', methods=['GET'])
def get_my_courses():
    if session.get('role') != 'employee': return jsonify({"success": False, "message": "Unauthorized"}), 401
    emp_id = session.get('emp_code')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT ca.course_name, ca.status, ca.progress, c.CourseFile
                FROM course_assigned ca
                JOIN course c ON ca.course_name = c.CourseName
                WHERE ca.emp_id = %s ORDER BY ca.assigned_date DESC
            """
            cursor.execute(sql, (emp_id,))
            courses = cursor.fetchall()
        return jsonify({"success": True, "courses": courses})
    except Exception as e:
        return jsonify({"success": False, "message": f"An error occurred: {e}"}), 500
    finally:
        conn.close()

@employee_bp.route('/start_assessment/<course_name>')
def start_assessment(course_name):
    if session.get('role') != 'employee': return redirect('/')
    emp_id = session.get('emp_code')
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("UPDATE course_assigned SET status = 'In Progress', progress = 50 WHERE emp_id = %s AND course_name = %s", (emp_id, course_name)); conn.commit()
        quiz_data = generate_quiz_questions(course_name)
        if not quiz_data.get('success'):
            return quiz_data.get('message', 'Could not generate the assessment.'), 500
        session['quiz_questions'] = quiz_data['questions']
        session['quiz_course_name'] = course_name
        return render_template('assessment_page.html', course_name=course_name, questions=quiz_data['questions'])
    except Exception as e:
        if conn and conn.open: conn.rollback()
        return "An internal error occurred.", 500
    finally:
        if conn and conn.open: conn.close()

@employee_bp.route('/submit_assessment', methods=['POST'])
def submit_assessment_answers():
    if session.get('role') != 'employee': return jsonify({"success": False, "message": "Unauthorized"}), 401
    emp_id = session.get('emp_code')
    answers = request.json.get('answers')
    questions = session.get('quiz_questions')
    course_name = session.get('quiz_course_name')
    if not all([answers, questions, course_name]): return redirect(url_for('employee.my_courses_page'))
    score = 0
    for i, question in enumerate(questions):
        if str(question['correctAnswerIndex']) == answers.get(str(i)): score += 1
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO assessment_marks (emp_id, course_name, marks_obtained) VALUES (%s, %s, %s)", (emp_id, course_name, score))
            if score >= 8:  # Changed from 7 to 8 (80% threshold)
                cursor.execute("UPDATE course_assigned SET status = 'Completed', progress = 100 WHERE emp_id = %s AND course_name = %s", (emp_id, course_name))
                cursor.execute("INSERT INTO course_completed (emp_id, course_name) VALUES (%s, %s)", (emp_id, course_name))
            else:
                 cursor.execute("UPDATE course_assigned SET status = 'In Progress', progress = 75 WHERE emp_id = %s AND course_name = %s", (emp_id, course_name))
            conn.commit()
            session.pop('quiz_questions', None); session.pop('quiz_course_name', None)
            session['last_assessment_result'] = {'score': score, 'total': len(questions), 'course_name': course_name}
            return jsonify({"success": True, "redirect_url": url_for('employee.assessment_result')})
    except Exception as e:
        conn.rollback()
        return jsonify({"success": False, "message": f"An error occurred: {e}"}), 500
    finally:
        conn.close()

@employee_bp.route('/assessment_result')
def assessment_result():
    if session.get('role') != 'employee' or 'last_assessment_result' not in session:
        return redirect(url_for('employee.my_courses_page'))
    result = session.pop('last_assessment_result', None)
    recommend_new = result['score'] >= 8
    return render_template('assessment_result.html', result=result, recommend_new=recommend_new)